"""CartRevive | Recovery Intelligence - Streamlit dashboard.

Read-only presentation layer over the generated snapshots in ``output/``.
Headline figures always come from the full-scale snapshot; the snapshot
selector only changes the operational views below the headline.

Run from the project root:  streamlit run app.py
Export buttons are hidden by default; add ``?exports=1`` to the URL to show them.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parent
OUTPUT_ROOT = ROOT / "output"
HEADLINE = "full_scale"
SNAPSHOTS = {
    "full_scale": "Full scale · 50,000 carts",
    "starter": "Starter · 2,500 carts",
}
TABLES = [
    "customer", "consent", "session", "cart", "cart_line", "cart_event",
    "campaign", "campaign_target", "message_template", "send_attempt",
    "send_event", "order", "attribution",
]
ISSUE_LABELS = {
    "message_after_optout": "Message sent after opt-out",
    "abandoned_after_purchase": "Abandonment after purchase",
    "attribution_double_count": "Duplicate order attribution",
    "stale_cart_total": "Stale cart total",
}
ISSUE_HELP = {
    "message_after_optout": "A reminder went out after the customer had withdrawn consent.",
    "abandoned_after_purchase": "The abandonment timestamp falls after the order was placed.",
    "attribution_double_count": "One order was credited to two campaigns, doubling its revenue.",
    "stale_cart_total": "The cart total was not refreshed after a quantity change.",
}
CART_STATUS_LABELS = {
    "purchased": "Purchased without abandoning",
    "abandoned": "Abandoned, not yet recovered",
    "recovered": "Returned after abandonment",
    "expired": "Expired, not recovered",
    "active": "Active",
}
DECISION_LABELS = {
    "sent": "Sent",
    "suppressed_purchased": "Suppressed · already purchased",
    "suppressed_no_consent": "Suppressed · no consent",
    "suppressed_quiet_hours": "Suppressed · quiet hours",
    "suppressed_freq_cap": "Suppressed · frequency cap",
}
CHANNEL_LABELS = {"email": "E-mail", "sms": "SMS"}
PALETTE = ["#1f4e8c", "#5b8fc7", "#9bbde3", "#2c3e50", "#7f8c9a"]
SMALL_SAMPLE = 100  # below this many contacted carts, a rate is shown as directional only

st.set_page_config(
    page_title="CartRevive | Recovery Intelligence",
    page_icon="🛒",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
    <style>
      .block-container { padding-top: 3.6rem; padding-bottom: 3rem; max-width: 1280px; }
      .hero { padding: 22px 26px; border: 1px solid #cfdcea; border-left: 6px solid #1f4e8c;
              border-radius: 10px; background: #ffffff; margin-bottom: 18px; }
      .hero-kicker { color: #1f4e8c; text-transform: uppercase; letter-spacing: .12em;
                     font-size: .72rem; font-weight: 700; }
      .hero-title { color: #13294b; font-size: 1.85rem; font-weight: 700; margin: .2rem 0; }
      .hero-sub { color: #3d4f66; font-size: .95rem; }
      .kpi { background: #ffffff; border: 1px solid #d5e0ec; border-radius: 10px; padding: 14px 16px;
             min-height: 104px; box-shadow: 0 1px 2px rgba(19,41,75,.05); }
      .kpi-label { color: #4a5b72; font-size: .78rem; font-weight: 600; text-transform: uppercase; letter-spacing: .04em; }
      .kpi-value { color: #13294b; font-size: 1.6rem; font-weight: 700; margin-top: 4px; }
      .kpi-note { color: #5a6b82; font-size: .78rem; margin-top: 2px; }
      .banner { border-radius: 8px; padding: 10px 14px; margin: 6px 0 14px 0; font-size: .9rem; color: #13294b; }
      .banner-info { background: #e8f1fb; border: 1px solid #b7cde6; }
      .banner-warn { background: #fff6e0; border: 1px solid #e8c46b; }
      .pill { display: inline-block; background: #e8f1fb; border: 1px solid #b7cde6; color: #13294b;
              padding: 3px 10px; border-radius: 999px; font-size: .76rem; font-weight: 600; margin-right: 6px; }
      .section-title { color: #13294b; font-size: 1.15rem; font-weight: 700; margin: 1.1rem 0 .4rem 0; }
      .footer { color: #5a6b82; font-size: .78rem; border-top: 1px solid #d5e0ec; margin-top: 2rem; padding-top: 10px; }
      /* Make the selected snapshot unmistakable: solid navy segment with white text */
      button[role="radio"][aria-checked="true"] {
          background: #1f4e8c !important; border-color: #1f4e8c !important; color: #ffffff !important; font-weight: 700;
      }
      button[role="radio"][aria-checked="true"] * { color: #ffffff !important; }
      button[role="radio"][aria-checked="false"] {
          background: #ffffff !important; border-color: #9fb6cf !important; color: #13294b !important;
      }
      button[role="radio"][aria-checked="false"] * { color: #13294b !important; }
      [data-testid="stWidgetLabel"] p { color: #13294b !important; font-weight: 700; }
      .stTabs [data-baseweb="tab"] { font-weight: 600; }
    </style>
    """,
    unsafe_allow_html=True,
)


# --------------------------------------------------------------------------- formatting helpers
def money(value: Any) -> str:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return "–"
    return "–" if math.isnan(x) else f"${x:,.2f}"


def money0(value: Any) -> str:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return "–"
    return "–" if math.isnan(x) else f"${x:,.0f}"


def num(value: Any) -> str:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return "–"
    return "–" if math.isnan(x) else f"{int(round(x)):,}"


def pct(value: Any, digits: int = 1) -> str:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return "–"
    return "–" if math.isnan(x) else f"{100 * x:.{digits}f}%"


def wilson(successes: float, trials: float, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a proportion. Honest about small samples."""
    if not trials or trials <= 0:
        return 0.0, 0.0
    p = successes / trials
    denom = 1 + z * z / trials
    centre = p + z * z / (2 * trials)
    half = z * math.sqrt(p * (1 - p) / trials + z * z / (4 * trials * trials))
    return max(0.0, (centre - half) / denom), min(1.0, (centre + half) / denom)


def col(df: pd.DataFrame, name: str) -> pd.Series:
    """Column access that never raises: returns an empty Series when the column is absent."""
    if isinstance(df, pd.DataFrame) and name in df.columns:
        return df[name]
    return pd.Series(dtype=object)


def present(df: pd.DataFrame | None) -> pd.DataFrame:
    return df if isinstance(df, pd.DataFrame) else pd.DataFrame()


# --------------------------------------------------------------------------- data access
def _read_csv(path: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(path)
    except (FileNotFoundError, pd.errors.EmptyDataError, OSError):
        return pd.DataFrame()


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


@st.cache_data(show_spinner="Loading snapshot…")
def load_snapshot(name: str) -> dict[str, Any]:
    base = OUTPUT_ROOT / name
    tables = {t: _read_csv(base / "data" / f"{t}.csv") for t in TABLES}
    metrics = {m: _read_csv(base / "metrics" / f"{m}.csv")
               for m in ("by_variant", "by_channel", "by_month", "per_send")}
    overall_raw = _read_json(base / "metrics" / "overall.json")
    if isinstance(overall_raw, list) and overall_raw and isinstance(overall_raw[0], dict):
        overall = overall_raw[0]
    elif isinstance(overall_raw, dict):
        overall = overall_raw
    else:
        overall = {}
    validation = _read_json(base / "validation_summary.json")
    manifest = _read_json(base / "manifest.json")
    return {
        "name": name,
        "available": base.exists() and not tables["cart"].empty,
        "tables": tables,
        "metrics": metrics,
        "overall": overall,
        "ground_truth": _read_csv(base / "ground_truth.csv"),
        "validation": validation if isinstance(validation, dict) else {},
        "manifest": manifest if isinstance(manifest, dict) else {},
    }


def prepare_sends(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    sends = present(tables.get("send_attempt")).copy()
    campaigns = present(tables.get("campaign"))
    if not sends.empty and not campaigns.empty and {"campaign_id", "name", "variant"} <= set(campaigns.columns):
        sends = sends.merge(campaigns[["campaign_id", "name", "variant"]], on="campaign_id", how="left")
    return sends


# --------------------------------------------------------------------------- presentation helpers
def hero(kicker: str, title: str, subtitle: str) -> None:
    st.markdown(
        f'<div class="hero"><div class="hero-kicker">{kicker}</div>'
        f'<div class="hero-title">{title}</div><div class="hero-sub">{subtitle}</div></div>',
        unsafe_allow_html=True,
    )


def banner(text: str, kind: str = "info") -> None:
    st.markdown(f'<div class="banner banner-{kind}">{text}</div>', unsafe_allow_html=True)


def kpi(label: str, value: str, note: str = "") -> str:
    return (f'<div class="kpi"><div class="kpi-label">{label}</div>'
            f'<div class="kpi-value">{value}</div><div class="kpi-note">{note}</div></div>')


def section(title: str) -> None:
    st.markdown(f'<div class="section-title">{title}</div>', unsafe_allow_html=True)


def show_fig(fig: go.Figure, height: int = 340, legend_bottom: bool = False) -> None:
    fig.update_layout(
        template="plotly_white", height=height,
        paper_bgcolor="#ffffff", plot_bgcolor="#ffffff",
        font=dict(color="#13294b", family="Arial, sans-serif", size=12),
        title=dict(font=dict(size=15, color="#13294b"), x=0.01),
        margin=dict(l=10, r=10, t=56, b=70 if legend_bottom or fig.data else 10),
        legend=dict(orientation="h", yanchor="top", y=-0.14, x=0.5, xanchor="center", title_text=""),
    )
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})


def table(df: pd.DataFrame, empty_message: str = "No rows match the current filters.") -> None:
    if df is None or df.empty:
        st.info(empty_message)
        return
    shown = df.copy()
    for c in shown.columns:
        if shown[c].dtype == object:
            shown[c] = shown[c].map(lambda v: "" if v is None or (isinstance(v, float) and math.isnan(v)) else str(v))
    st.dataframe(shown, width="stretch", hide_index=True)


def export_button(label: str, data: bytes, file_name: str, mime: str) -> None:
    """Export buttons are shown only when the URL contains ?exports=1 (off during presentations)."""
    if st.query_params.get("exports") == "1":
        st.download_button(label, data, file_name=file_name, mime=mime)


# --------------------------------------------------------------------------- snapshot selection
top_left, top_right = st.columns([1.35, 1])
with top_left:
    st.markdown("**Dataset snapshot**")
    choice = st.segmented_control(
        "Dataset snapshot",
        options=list(SNAPSHOTS),
        format_func=lambda k: SNAPSHOTS[k],
        default=HEADLINE,
        key="snapshot_choice",
        label_visibility="collapsed",
    )
selected_name = choice or HEADLINE

headline = load_snapshot(HEADLINE)
current = load_snapshot(selected_name)

with top_right:
    seed = current["manifest"].get("seed", "–")
    scale = current["manifest"].get("scale", "–")
    st.markdown(
        f"<div style='padding-top:30px'><span class='pill'>Viewing: {SNAPSHOTS[selected_name]}</span>"
        f"<span class='pill'>Seed {seed}</span><span class='pill'>Scale {scale}</span></div>",
        unsafe_allow_html=True,
    )

if not headline["available"]:
    st.error(
        "The full-scale snapshot was not found. Run `python -m cartrecovery.services.pipeline "
        "--scale 1 --out output/full_scale --validate` from the project root, then refresh."
    )
    st.stop()

if not current["available"]:
    st.warning(f"The '{SNAPSHOTS[selected_name]}' snapshot is missing from output/. Showing headline data only.")

hero(
    "CARTREVIVE / RECOVERY INTELLIGENCE",
    "Turn abandoned carts into measurable revenue",
    "Campaign recovery, consent-aware messaging and exception review across the synthetic commerce dataset. "
    "Every figure is derived from documented rules and reconciled to a fixed-seed snapshot.",
)

tab_overview, tab_campaign, tab_cart, tab_exceptions, tab_quality = st.tabs(
    ["Overview", "Campaign performance", "Cart explorer", "Exception centre", "Data quality"]
)


# =========================================================================== OVERVIEW (headline = full scale)
with tab_overview:
    o = headline["overall"]
    contacted = o.get("unique_carts_contacted")
    recovered = o.get("recovered_carts")
    lo, hi = wilson(recovered or 0, contacted or 0)
    st.markdown(
        '<div class="section-title" style="margin-top:0">Headline · full-scale snapshot</div>',
        unsafe_allow_html=True,
    )
    cards = [
        kpi("Dispatched sends", num(o.get("sends")), "Consent and frequency rules applied"),
        kpi("Carts contacted", num(contacted), "Distinct abandoned carts"),
        kpi("Campaign-recovered carts", num(recovered), f"Recovery rate {pct(o.get('recovery_rate'))} "
            f"(95% CI {pct(lo)}–{pct(hi)})"),
        kpi("Revenue recovered", money0(o.get("revenue_recovered")), "Same-cart recoveries, placed orders"),
        kpi("Revenue per send", money(o.get("revenue_attribution_per_send")), "Attributed revenue ÷ sends"),
    ]
    cols = st.columns(5)
    for c, html in zip(cols, cards):
        with c:
            st.markdown(html, unsafe_allow_html=True)
    st.caption(
        "Campaign metrics use the clean baseline captured before the four labelled exception cases were injected. "
        "Operational counts below include those cases."
    )

    monthly = present(headline["metrics"].get("by_month"))
    decision_src = prepare_sends(headline["tables"])
    c1, c2 = st.columns([1.3, 1])
    with c1:
        if not monthly.empty and {"month", "revenue_recovered"} <= set(monthly.columns):
            fig = px.area(monthly, x="month", y="revenue_recovered", markers=True,
                          title="Revenue recovered by month (USD)",
                          labels={"month": "Month", "revenue_recovered": "Revenue recovered (USD)"})
            fig.update_traces(line=dict(color="#1f4e8c", width=3), fillcolor="rgba(31,78,140,0.12)")
            show_fig(fig)
        else:
            st.info("Monthly metrics are not available for this snapshot.")
    with c2:
        dec = col(decision_src, "decision").value_counts()
        if not dec.empty:
            dfd = dec.rename_axis("decision").reset_index(name="attempts")
            dfd["label"] = dfd["decision"].map(DECISION_LABELS).fillna(dfd["decision"])
            fig = px.pie(dfd, names="label", values="attempts", hole=0.6, title="Send decision mix (full scale)",
                         color_discrete_sequence=PALETTE)
            fig.update_traces(textinfo="percent", textposition="inside",
                              marker=dict(line=dict(color="#ffffff", width=2)))
            show_fig(fig, height=360, legend_bottom=True)
        else:
            st.info("Send decisions are not available.")

    c3, c4 = st.columns(2)
    with c3:
        by_var = present(headline["metrics"].get("by_variant"))
        if not by_var.empty and {"variant", "revenue_recovered"} <= set(by_var.columns):
            fig = px.bar(by_var.sort_values("revenue_recovered"), x="revenue_recovered", y="variant",
                         orientation="h", title="Revenue recovered by campaign variant (USD)",
                         labels={"revenue_recovered": "Revenue recovered (USD)", "variant": "Variant"},
                         text=by_var.sort_values("revenue_recovered")["revenue_recovered"].map(money0))
            fig.update_traces(marker_color="#1f4e8c", textposition="outside", cliponaxis=False)
            show_fig(fig)
    with c4:
        by_ch = present(headline["metrics"].get("by_channel"))
        if not by_ch.empty and {"channel", "recovered_carts", "unique_carts_contacted"} <= set(by_ch.columns):
            rates, err_hi, err_lo = [], [], []
            for _, r in by_ch.iterrows():
                rate = (r["recovered_carts"] / r["unique_carts_contacted"]) if r["unique_carts_contacted"] else 0
                a, b = wilson(r["recovered_carts"], r["unique_carts_contacted"])
                rates.append(rate)
                err_hi.append(b - rate)
                err_lo.append(rate - a)
            plot = by_ch.assign(rate=rates, label=by_ch["channel"].map(CHANNEL_LABELS).fillna(by_ch["channel"]))
            fig = px.bar(plot, x="label", y="rate", title="Recovery rate by channel, with 95% confidence interval",
                         text=plot["rate"].map(lambda v: f"{100 * v:.1f}%"))
            fig.update_traces(marker_color=["#1f4e8c", "#5b8fc7"][: len(plot)], textposition="outside",
                              error_y=dict(type="data", symmetric=False, array=err_hi, arrayminus=err_lo,
                                           color="#13294b"))
            fig.update_yaxes(tickformat=".0%", title="Recovery rate")
            fig.update_xaxes(title="Channel")
            show_fig(fig)

    section("Channel comparison · full scale")
    if not by_ch.empty:
        rows = []
        for _, r in by_ch.iterrows():
            a, b = wilson(r.get("recovered_carts", 0), r.get("unique_carts_contacted", 0))
            rows.append({
                "Channel": CHANNEL_LABELS.get(r.get("channel"), r.get("channel")),
                "Sends": num(r.get("sends")),
                "Carts contacted": num(r.get("unique_carts_contacted")),
                "Recovered carts": num(r.get("recovered_carts")),
                "Recovery rate": pct(r.get("recovery_rate")),
                "95% CI": f"{pct(a)} – {pct(b)}",
                "Revenue recovered": money0(r.get("revenue_recovered")),
            })
        table(pd.DataFrame(rows))
        st.caption("Channel rates are not additive: a cart contacted on both channels is counted in each.")

    section("Operational snapshot · full scale")
    ht = headline["tables"]
    cart = present(ht["cart"])
    ab_count = int(col(cart, "abandoned_at").notna().sum()) if not cart.empty else 0
    a, b, c, d = st.columns(4)
    a.metric("Customers", num(len(present(ht["customer"]))))
    b.metric("Carts with an abandonment timestamp", num(ab_count), help="Includes the labelled exception cases.")
    c.metric("Campaign waves", num(len(present(ht["campaign"]))))
    d.metric("Labelled exception cases", num(len(headline["ground_truth"])))


# =========================================================================== CAMPAIGN PERFORMANCE (selected snapshot)
with tab_campaign:
    hero("CARTREVIVE / CAMPAIGN LAB", "Campaign performance",
         "Compare message variants, channels and campaign waves. Filters apply to the selected snapshot.")
    cur_t = current["tables"]
    campaigns = present(cur_t["campaign"])
    sends_all = prepare_sends(cur_t)
    sent_all = sends_all[sends_all.get("decision", pd.Series(dtype=object)).eq("sent")] if not sends_all.empty else sends_all

    cur_ch = present(current["metrics"].get("by_channel"))
    sms_row = cur_ch[cur_ch["channel"].eq("sms")] if not cur_ch.empty and "channel" in cur_ch.columns else pd.DataFrame()
    sms_carts = int(sms_row["unique_carts_contacted"].iloc[0]) if not sms_row.empty else 0
    if selected_name != HEADLINE:
        banner(
            f"<b>Starter snapshot: small samples.</b> SMS rests on {num(sms_carts)} contacted carts across "
            f"{num(len(campaigns[campaigns['channel'].eq('sms')])) if not campaigns.empty and 'channel' in campaigns.columns else '–'} waves, "
            f"so single-wave rates swing widely. Treat these figures as directional. "
            f"For conclusions, use the full-scale headline on the Overview tab.", "warn")
    else:
        banner("Full-scale snapshot: every wave has enough sends for stable rates.", "info")

    variants = sorted(col(campaigns, "variant").dropna().unique().tolist())
    channels = sorted(col(campaigns, "channel").dropna().unique().tolist())
    waves = col(campaigns, "name").dropna().tolist()
    f1, f2, f3 = st.columns([1, 1, 1.6])
    with f1:
        chosen_variants = st.multiselect("Campaign variants", variants, default=variants, key="cv")
    with f2:
        chosen_channels = st.multiselect("Channels", channels, default=channels, key="cc",
                                         format_func=lambda c: CHANNEL_LABELS.get(c, c))
    with f3:
        chosen_waves = st.multiselect("Campaign waves", waves, default=[], key="cw",
                                      placeholder="All campaign waves")

    view = sent_all.copy()
    if not view.empty:
        if chosen_variants:
            view = view[view["variant"].isin(chosen_variants)]
        if chosen_channels:
            view = view[view["channel"].isin(chosen_channels)]
        if chosen_waves:
            view = view[view["name"].isin(chosen_waves)]

    labelled_sends = 0
    if not current["ground_truth"].empty and "entity" in current["ground_truth"].columns:
        labelled_sends = int(current["ground_truth"]["entity"].eq("send_attempt").sum())
    baseline_sends = current["overall"].get("sends")
    if labelled_sends and baseline_sends is not None:
        st.caption(
            f"Operational records include {labelled_sends} labelled exception send(s). The clean baseline "
            f"used for headline KPIs reports {num(baseline_sends)} dispatched sends for this snapshot."
        )
    m1, m2, m3 = st.columns(3)
    m1.metric("Dispatched sends (filtered)", num(len(view)))
    m2.metric("Unique carts contacted (filtered)", num(view["cart_id"].nunique() if not view.empty else 0))
    m3.metric("Campaign waves represented", num(view["campaign_id"].nunique() if not view.empty else 0))

    # Every wave appears, including any wave with zero dispatched sends.
    wave_base = campaigns.copy()
    if chosen_variants:
        wave_base = wave_base[wave_base["variant"].isin(chosen_variants)]
    if chosen_channels:
        wave_base = wave_base[wave_base["channel"].isin(chosen_channels)]
    if chosen_waves:
        wave_base = wave_base[wave_base["name"].isin(chosen_waves)]
    if not view.empty:
        counts = view.groupby("campaign_id").agg(sends=("send_id", "count"), carts=("cart_id", "nunique")).reset_index()
    else:
        counts = pd.DataFrame(columns=["campaign_id", "sends", "carts"])
    wave_table = wave_base[["campaign_id", "name", "variant", "channel"]].merge(counts, on="campaign_id", how="left")
    wave_table[["sends", "carts"]] = wave_table[["sends", "carts"]].fillna(0).astype(int)
    wave_table = wave_table.sort_values(["channel", "name"]).reset_index(drop=True)
    wave_table["sample"] = wave_table["sends"].map(
        lambda n: "No sends" if n == 0 else ("Small sample (<30)" if n < 30 else "Adequate"))

    left, right = st.columns([1.15, 1])
    with left:
        if not wave_table.empty:
            fig = px.bar(wave_table.sort_values("sends"), x="sends", y="name", color="channel",
                         orientation="h", title="Dispatched sends by campaign wave",
                         labels={"sends": "Dispatched sends", "name": "Wave", "channel": "Channel"},
                         color_discrete_map={"email": "#1f4e8c", "sms": "#9bbde3"},
                         hover_data={"campaign_id": True, "variant": True, "carts": True, "channel": False})
            fig.update_yaxes(title="")
            show_fig(fig, height=max(360, 22 * len(wave_table) + 90))
        else:
            st.info("No campaign waves match these filters.")
    with right:
        by_var_cur = present(current["metrics"].get("by_variant"))
        if not by_var_cur.empty and {"sends", "recovery_rate", "variant"} <= set(by_var_cur.columns):
            vm = by_var_cur[by_var_cur["variant"].isin(chosen_variants)] if chosen_variants else by_var_cur
            if not vm.empty:
                fig = px.scatter(vm, x="sends", y="recovery_rate", size="revenue_recovered", color="variant",
                                 hover_name="variant", size_max=50, title="Variant efficiency: volume vs recovery rate",
                                 labels={"sends": "Dispatched sends", "recovery_rate": "Recovery rate",
                                         "revenue_recovered": "Revenue recovered", "variant": "Variant"},
                                 color_discrete_sequence=PALETTE)
                fig.update_yaxes(tickformat=".1%", title="Recovery rate")
                fig.update_xaxes(title="Dispatched sends")
                show_fig(fig, height=max(360, 22 * len(wave_table) + 90))

    section("Campaign wave details")
    table(wave_table.rename(columns={
        "campaign_id": "Campaign", "name": "Wave", "variant": "Variant",
        "channel": "Channel", "sends": "Sends", "carts": "Carts", "sample": "Sample size"}))
    st.caption("Each wave is one variant on one channel for one period. "
               "Waves with zero sends are listed explicitly rather than omitted.")
    export_button("Download filtered campaign sends (CSV)",
                  view.to_csv(index=False).encode("utf-8"),
                  "cartrevive_filtered_sends.csv", "text/csv")


# =========================================================================== CART EXPLORER
with tab_cart:
    hero("CARTREVIVE / CUSTOMER JOURNEY", "Cart explorer",
         "Investigate a cart through its line items, lifecycle events, contact attempts and orders.")
    carts = present(current["tables"]["cart"]).copy()
    if carts.empty:
        st.warning("The cart table is not available in this snapshot.")
    else:
        carts["status_label"] = carts["status"].map(CART_STATUS_LABELS).fillna(carts["status"].astype(str))
        status_options = sorted(carts["status"].dropna().unique().tolist())
        q1, q2 = st.columns([1, 1.2])
        with q1:
            search_id = st.text_input("Search cart ID", placeholder="CART-2026-000123", key="cart_search")
        with q2:
            status_filter = st.multiselect("Cart status", status_options, default=status_options,
                                           key="cart_status",
                                           format_func=lambda s: CART_STATUS_LABELS.get(s, s))
        filtered = carts[carts["status"].isin(status_filter)] if status_filter else carts.iloc[0:0]
        if search_id.strip():
            filtered = filtered[filtered["cart_id"].astype(str).str.contains(search_id.strip(), case=False, na=False)]
        st.caption(
            f"Showing {len(filtered):,} of {len(carts):,} carts. "
            "'Returned after abandonment' includes carts that came back without a campaign message, "
            "so it is larger than the campaign-recovered count on the Overview."
        )
        table(filtered.head(300)[[c for c in ["cart_id", "customer_id", "status_label", "created_at",
                                             "abandoned_at", "item_count", "total_amount"] if c in filtered.columns]]
              .rename(columns={"status_label": "status"}),
              "No carts match these filters.")

        if not filtered.empty:
            cart_ids = filtered["cart_id"].head(500).tolist()
            selected_cart = st.selectbox("Open cart journey", cart_ids, key="cart_pick")
            cart = carts[carts["cart_id"].eq(selected_cart)].iloc[0]
            cur_t = current["tables"]
            customer = present(cur_t["customer"])
            customer = customer[customer["customer_id"].eq(cart["customer_id"])] if not customer.empty else customer
            lines = present(cur_t["cart_line"])
            lines = lines[lines["cart_id"].eq(selected_cart)] if not lines.empty else lines
            events = present(cur_t["cart_event"])
            events = events[events["cart_id"].eq(selected_cart)].copy() if not events.empty else events
            sends_all = prepare_sends(cur_t)
            attempts = sends_all[sends_all["cart_id"].eq(selected_cart)] if not sends_all.empty else sends_all
            orders = present(cur_t["order"])
            orders = orders[orders["cart_id"].eq(selected_cart)] if not orders.empty else orders

            section(f"Journey · {selected_cart}")
            p1, p2, p3, p4 = st.columns(4)
            p1.metric("Status", CART_STATUS_LABELS.get(str(cart["status"]), str(cart["status"])))
            p2.metric("Cart value", money(cart.get("total_amount")))
            p3.metric("Line items", num(len(lines)))
            p4.metric("Contact attempts", num(len(attempts)))

            if not customer.empty:
                with st.expander("Customer profile"):
                    table(customer)

            lcol, rcol = st.columns(2)
            with lcol:
                st.markdown("**Cart line items**")
                table(lines, "No line items recorded for this cart.")
                st.markdown("**Orders**")
                table(orders, "No order placed for this cart.")
            with rcol:
                st.markdown("**Cart event timeline**")
                if not events.empty:
                    events["event_ts"] = pd.to_datetime(events["event_ts"], errors="coerce")
                    events = events.sort_values("event_ts")
                    if events["event_ts"].notna().any():
                        fig = px.scatter(events, x="event_ts", y="event_type", color="event_type",
                                         color_discrete_sequence=PALETTE, title="Lifecycle events",
                                         labels={"event_ts": "Time (UTC)", "event_type": "Event"})
                        fig.update_yaxes(title="")
                        show_fig(fig, height=300)
                    table(events)
                else:
                    st.info("No lifecycle events recorded for this cart.")
                st.markdown("**Contact history**")
                table(attempts, "This cart was not contacted by any campaign.")
        else:
            st.info("Adjust the filters to open a cart journey.")


# =========================================================================== EXCEPTION CENTRE
with tab_exceptions:
    hero("CARTREVIVE / GOVERNANCE & REVIEW", "Exception centre",
         "Review the four deliberately injected business-rule exceptions, inspect their evidence and separate "
         "known test cases from unexplained violations.")
    gt = present(current["ground_truth"]).copy()
    validation = current["validation"]
    if gt.empty:
        st.info("No ground-truth exception file is available in this snapshot.")
    else:
        counts = gt["issue_type"].value_counts().rename_axis("issue_type").reset_index(name="cases")
        c1, c2 = st.columns([1, 1.25])
        with c1:
            fig = px.pie(counts.assign(label=counts["issue_type"].map(ISSUE_LABELS).fillna(counts["issue_type"])),
                         names="label", values="cases", hole=0.6, title="Known exception distribution",
                         color_discrete_sequence=PALETTE)
            show_fig(fig, height=330, legend_bottom=True)
        with c2:
            st.markdown("#### Exception types")
            for _, row in counts.iterrows():
                label = ISSUE_LABELS.get(row["issue_type"], row["issue_type"])
                st.markdown(
                    f"<div class='kpi' style='min-height:0;margin-bottom:8px'>"
                    f"<div class='kpi-label'>{label}</div>"
                    f"<div class='kpi-value' style='font-size:1.2rem'>{int(row['cases']):,} labelled cases</div>"
                    f"<div class='kpi-note'>{ISSUE_HELP.get(row['issue_type'], '')}</div></div>",
                    unsafe_allow_html=True)

        options = ["All exception types"] + counts["issue_type"].tolist()
        selected_issue = st.selectbox("Filter exception type", options, key="gt_filter",
                                      format_func=lambda x: ISSUE_LABELS.get(x, x))
        shown = gt if selected_issue == "All exception types" else gt[gt["issue_type"].eq(selected_issue)]
        section("Ground-truth case register")
        table(shown)
        export_button("Download exception register (CSV)", shown.to_csv(index=False).encode("utf-8"),
                      "cartrevive_exception_register.csv", "text/csv")

        section("Evidence lookup")
        case_ids = shown["gt_id"].astype(str).tolist() if "gt_id" in shown.columns else []
        if case_ids:
            case_id = st.selectbox("Select case", case_ids, key="gt_case")
            case = gt[gt["gt_id"].astype(str).eq(case_id)].iloc[0]
            st.markdown(f"**{ISSUE_LABELS.get(case['issue_type'], case['issue_type'])}** · `{case['entity_id']}`")
            st.info(str(case.get("note", "")))
            ht = current["tables"]
            entity = str(case.get("entity", ""))
            if entity == "send_attempt":
                sends_all = prepare_sends(ht)
                evidence = sends_all[sends_all["send_id"].eq(case["entity_id"])]
                st.markdown("**Delivery events for this send**")
                table(present(ht["send_event"])[present(ht["send_event"])["send_id"].eq(case["entity_id"])])
            elif entity == "order":
                evidence = present(ht["order"])
                evidence = evidence[evidence["order_id"].eq(case["entity_id"])]
                st.markdown("**Attribution records for this order**")
                attr = present(ht["attribution"])
                table(attr[attr["order_id"].eq(case["entity_id"])] if not attr.empty else attr)
            else:
                evidence = present(ht["cart"])
                evidence = evidence[evidence["cart_id"].eq(case["entity_id"])]
                if case["issue_type"] == "stale_cart_total":
                    st.markdown("**Cart line items used for reconciliation**")
                    lines = present(ht["cart_line"])
                    table(lines[lines["cart_id"].eq(case["entity_id"])] if not lines.empty else lines)
                if case["issue_type"] == "abandoned_after_purchase":
                    st.markdown("**Purchase and abandonment lifecycle evidence**")
                    evs = present(ht["cart_event"])
                    table(evs[evs["cart_id"].eq(case["entity_id"])] if not evs.empty else evs)
            st.markdown("**Primary entity record**")
            table(evidence)
        else:
            st.info("No cases match this filter.")

        section("Validation control totals")
        v1, v2, v3, v4 = st.columns(4)
        v1.metric("Validation groups", num(validation.get("check_groups_executed", 0)))
        v2.metric("Rule checks", num(validation.get("rule_checks_executed", 0)))
        v3.metric("Detected violations", num(validation.get("total_violations", 0)))
        v4.metric("Ground-truth labels", num(len(gt)))
        st.caption(
            "The injected anomalies are expected test fixtures. A PASS reconciliation means detected violations "
            "match the separate ground-truth labels. It does not mean the corrupted records are clean."
        )


# =========================================================================== DATA QUALITY
with tab_quality:
    hero("CARTREVIVE / DATA GOVERNANCE", "Data quality and lineage",
         "Dataset scale, validation execution, table volumes and artifact provenance behind every figure.")
    manifest = current["manifest"]
    validation = current["validation"]
    a, b, c, d = st.columns(4)
    a.metric("Dataset scale", str(manifest.get("scale", "–")))
    b.metric("Artifacts tracked", num(manifest.get("artifact_count", 0)))
    c.metric("Rule checks executed", num(validation.get("rule_checks_executed", 0)))
    d.metric("Ground-truth cases", num(len(current["ground_truth"])))

    section("Relational table inventory")
    inventory = pd.DataFrame([
        {"Table": name, "Rows": len(df), "Columns": len(df.columns)}
        for name, df in current["tables"].items()
    ])
    left, right = st.columns([1, 1.2])
    with left:
        table(inventory)
    with right:
        if not inventory.empty and inventory["Rows"].sum() > 0:
            fig = px.bar(inventory.sort_values("Rows"), x="Rows", y="Table", orientation="h",
                         title="Rows by relational table", text="Rows",
                         labels={"Rows": "Rows", "Table": "Table"})
            fig.update_traces(marker_color="#1f4e8c", texttemplate="%{text:,}", textposition="outside",
                              cliponaxis=False)
            fig.update_yaxes(title="")
            show_fig(fig, height=430)

    section("Validation groups")
    groups = pd.DataFrame(validation.get("check_groups", []))
    if not groups.empty and "violation_count" in groups.columns:
        groups = groups.copy()
        groups["result"] = groups["violation_count"].map(
            lambda x: "Expected injected cases" if int(x) else "No violations")
        fig = px.bar(groups, x="check_group", y="violation_count", color="result",
                     color_discrete_map={"No violations": "#1f4e8c", "Expected injected cases": "#e0a526"},
                     title="Detected violations by validation group",
                     labels={"check_group": "Check group", "violation_count": "Violations", "result": "Result"})
        fig.update_layout(xaxis_tickangle=-25)
        fig.update_xaxes(title="")
        show_fig(fig, height=360)
        table(groups)
    else:
        st.info("Validation groups are not available for this snapshot.")

    checks = pd.DataFrame(validation.get("checks", []))
    if not checks.empty and "status" in checks.columns:
        with st.expander("Inspect individual validation rules"):
            statuses = sorted(checks["status"].dropna().unique().tolist())
            status_filter = st.multiselect("Rule status", statuses, default=statuses, key="rule_status")
            table(checks[checks["status"].isin(status_filter)] if status_filter else checks.iloc[0:0])

    artifacts = pd.DataFrame(manifest.get("artifacts", []))
    if not artifacts.empty:
        with st.expander("Artifact manifest and SHA-256 checksums"):
            table(artifacts)
            export_button("Download artifact manifest (JSON)",
                          json.dumps(manifest, indent=2).encode("utf-8"),
                          "cartrevive_manifest.json", "application/json")

st.markdown(
    "<div class='footer'>CartRevive · Synthetic recovery operations console · "
    "Deterministic snapshot · No live customer messaging</div>",
    unsafe_allow_html=True,
)
