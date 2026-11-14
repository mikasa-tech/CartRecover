"""Campaign performance metrics computed from the clean baseline tables.

Metric definitions:
- A dispatched send is a send_attempt with decision == "sent".
- A converted order is an attributed order whose status is "placed".
- A recovered cart is an attributed placed order for the same cart targeted by the credited send,
  where that cart's final baseline status is "recovered".
- Recovery rate = distinct attributed recovered carts / distinct carts contacted.
- Revenue recovered = attributed, placed-order revenue from those same-cart recoveries.
- Attributed revenue = attributed, placed-order revenue (all cart outcomes).
- Revenue per send = attributed revenue / dispatched sends.
The month dimension is the UTC calendar month of sent_at. Each breakdown is
computed independently; revenue totals reconcile because each baseline order has
at most one deterministic attribution. Counts/rates are not additive across
different dimensions or segments.
"""
from __future__ import annotations

import pandas as pd

DIMENSIONS = {
    "variant": ["variant"],
    "channel": ["channel"],
    "month": ["month"],
}


def _empty_summary(columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame(columns=columns + [
        "sends", "unique_carts_contacted", "attributed_orders",
        "recovered_carts", "recovery_rate", "revenue_recovered",
        "attributed_revenue", "revenue_attribution_per_send",
    ])


def compute_metrics(tables: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Return per-send metrics and variant/channel/month aggregate breakdowns."""
    required = {"send_attempt", "campaign", "order", "attribution", "cart"}
    missing = required - set(tables)
    if missing:
        raise ValueError(f"Missing required tables: {', '.join(sorted(missing))}")

    sends = tables["send_attempt"].loc[
        tables["send_attempt"]["decision"].eq("sent")
    ].copy()
    campaigns = tables["campaign"][["campaign_id", "variant"]]
    sends = sends.merge(campaigns, on="campaign_id", how="left", validate="many_to_one")
    if sends["variant"].isna().any():
        raise ValueError("A dispatched send references a missing campaign")

    sends["month"] = pd.to_datetime(sends["sent_at"], errors="raise").dt.strftime("%Y-%m")

    attr = tables["attribution"].copy()
    orders = tables["order"][["order_id", "cart_id", "ordered_at", "total_amount", "status"]]
    carts = tables["cart"][["cart_id", "status"]].rename(columns={"status": "cart_status"})
    if attr.empty:
        attributed = pd.DataFrame(columns=[
            "send_id", "order_id", "cart_id", "total_amount", "status",
            "cart_status", "credit", "sent_cart_id", "recognized_revenue",
            "recovered_revenue", "recovered_flag", "placed_flag", "recovered_cart_id",
        ])
    else:
        attributed = attr[["send_id", "order_id", "credit"]].merge(
            orders, on="order_id", how="left", validate="many_to_one"
        ).merge(carts, on="cart_id", how="left", validate="many_to_one")
        if attributed[["cart_id", "total_amount", "cart_status"]].isna().any().any():
            raise ValueError("Attribution references a missing order or cart")
        if attributed["order_id"].duplicated().any():
            raise ValueError("Baseline attribution must credit each order at most once")
        attributed = attributed.merge(
            sends[["send_id", "cart_id"]].rename(columns={"cart_id": "sent_cart_id"}),
            on="send_id", how="left", validate="many_to_one",
        )
        if attributed["sent_cart_id"].isna().any():
            raise ValueError("Attribution references a send that was not dispatched")

    # Revenue is realized only for placed orders; cancelled/refunded orders remain
    # in the source data but do not inflate realized campaign revenue.
    attributed["recognized_revenue"] = (
        attributed["total_amount"].where(attributed["status"].eq("placed"), 0.0)
        * attributed["credit"].astype(float)
    )
    attributed["recovered_flag"] = (
        attributed["status"].eq("placed")
        & attributed["cart_status"].eq("recovered")
        & attributed["cart_id"].eq(attributed["sent_cart_id"])
    ).astype(int)
    attributed["recovered_cart_id"] = attributed["cart_id"].where(attributed["recovered_flag"].eq(1))
    attributed["recovered_revenue"] = attributed["recognized_revenue"].where(
        attributed["recovered_flag"].eq(1), 0.0
    )
    attributed["placed_flag"] = attributed["status"].eq("placed").astype(int)

    # One row per dispatched send, including sends with no attributed conversion.
    # A click can be attributed to a later order from the same customer, so aggregate
    # the credited orders by send_id before joining to preserve exactly one row/send.
    if attributed.empty:
        per_send_attribution = pd.DataFrame(columns=[
            "send_id", "attributed_order_count", "recovered_cart_count",
            "revenue_attributed", "revenue_recovered",
        ])
    else:
        per_send_attribution = attributed.groupby("send_id", as_index=False).agg(
            attributed_order_count=("placed_flag", "sum"),
            recovered_cart_count=("recovered_flag", "sum"),
            revenue_attributed=("recognized_revenue", "sum"),
            revenue_recovered=("recovered_revenue", "sum"),
        )
    per_send = sends[[
        "send_id", "campaign_id", "cart_id", "customer_id", "channel", "variant", "month", "sent_at"
    ]].merge(
        per_send_attribution, on="send_id", how="left", validate="one_to_one"
    )
    for col in ("attributed_order_count", "recovered_cart_count", "revenue_attributed", "revenue_recovered"):
        per_send[col] = per_send[col].fillna(0)
    per_send["attributed_order_count"] = per_send["attributed_order_count"].astype(int)
    per_send["recovered_cart_count"] = per_send["recovered_cart_count"].astype(int)

    # The aggregate helper starts from sent attempts so zero-conversion groups remain visible.
    def aggregate(group_cols: list[str]) -> pd.DataFrame:
        base = sends.groupby(group_cols, dropna=False).agg(
            sends=("send_id", "nunique"),
            unique_carts_contacted=("cart_id", "nunique"),
        ).reset_index()
        if attributed.empty:
            conv = pd.DataFrame(columns=group_cols + [
                "attributed_orders", "recovered_carts", "revenue_recovered", "attributed_revenue"
            ])
        else:
            attributed_sends = attributed.merge(
                sends[["send_id", "variant", "channel", "month"]],
                on="send_id", how="inner", validate="many_to_one"
            )
            attributed_sends = attributed_sends[attributed_sends.status.eq("placed")]
            conv = attributed_sends.groupby(group_cols, dropna=False).agg(
                attributed_orders=("order_id", "nunique"),
                recovered_carts=("recovered_cart_id", "nunique"),
                revenue_recovered=("recovered_revenue", "sum"),
                attributed_revenue=("recognized_revenue", "sum"),
            ).reset_index()
        result = base.merge(conv, on=group_cols, how="left", validate="one_to_one")
        for col in ("attributed_orders", "recovered_carts", "revenue_recovered", "attributed_revenue"):
            result[col] = result[col].fillna(0)
        result["recovery_rate"] = (
            result["recovered_carts"] / result["unique_carts_contacted"].replace(0, pd.NA)
        ).fillna(0.0)
        result["revenue_attribution_per_send"] = (
            result["attributed_revenue"] / result["sends"].replace(0, pd.NA)
        ).fillna(0.0)
        result["revenue_recovered"] = result["revenue_recovered"].round(2)
        result["attributed_revenue"] = result["attributed_revenue"].round(2)
        result["revenue_attribution_per_send"] = result["revenue_attribution_per_send"].round(4)
        return result.sort_values(group_cols).reset_index(drop=True)

    result = {"per_send": per_send}
    for name, cols in DIMENSIONS.items():
        result[f"by_{name}"] = aggregate(cols)

    # Global control totals are intentionally derived from per-send attribution.
    result["overall"] = pd.DataFrame([{
        "sends": int(sends["send_id"].nunique()),
        "unique_carts_contacted": int(sends["cart_id"].nunique()),
        "attributed_orders": int(per_send["attributed_order_count"].sum()),
        "recovered_carts": int(per_send["recovered_cart_count"].sum()),
        "recovery_rate": (
            float(per_send["recovered_cart_count"].sum() / sends["cart_id"].nunique())
            if sends["cart_id"].nunique() else 0.0
        ),
        "revenue_recovered": round(float(per_send["revenue_recovered"].sum()), 2),
        "attributed_revenue": round(float(per_send["revenue_attributed"].sum()), 2),
        "revenue_attribution_per_send": (
            round(float(per_send["revenue_attributed"].sum() / sends["send_id"].nunique()), 4)
            if sends["send_id"].nunique() else 0.0
        ),
    }])
    return result


def validate_metrics(metrics: dict[str, pd.DataFrame], tolerance: float = 0.01) -> None:
    """Raise ValueError if the independent dimension totals do not reconcile."""
    overall = metrics["overall"].iloc[0]
    for key in ("by_variant", "by_channel", "by_month"):
        frame = metrics[key]
        for col in ("sends", "attributed_orders", "recovered_carts"):
            if int(frame[col].sum()) != int(overall[col]):
                raise ValueError(f"{key}.{col} does not reconcile to overall total")
        for col in ("revenue_recovered", "attributed_revenue"):
            if abs(float(frame[col].sum()) - float(overall[col])) > tolerance:
                raise ValueError(f"{key}.{col} does not reconcile to overall total")
