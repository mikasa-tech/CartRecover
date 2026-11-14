"""Tier 2: injects the 4 labelled edge cases into a CLEAN dataset and emits the separate ground-truth table.

Each anomaly mutates only the rows it labels, so the validator can be checked for exact recall/precision.
Business-logic modules (generator, campaigns, validator) never import this file.
"""
from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd

from cartrecovery.domain import rules as r
from cartrecovery.domain.dictionary import GROUND_TRUTH_FIELDS


def inject(cfg: dict, t: dict[str, pd.DataFrame], scale: float = 1.0):
    rng = np.random.default_rng([cfg["seed"], 4])
    n = {k: max(1, round(v * scale)) for k, v in cfg["anomalies"].items()}
    t = {k: v.copy() for k, v in t.items()}
    end, yr = r.parse(cfg["sim_end"]), cfg["year"]
    lat = timedelta(minutes=cfg["campaign"]["dispatch_latency_minutes"])
    q, win = cfg["rules"]["quiet_hours"], cfg["rules"]["abandonment_window_minutes"]
    gt: list[tuple] = []
    used_carts: set[str] = set()

    def mark(issue, entity, eid, expected, note):
        gt.append((issue, entity, eid, expected, note))

    # 1 ── message sent after opt-out: a correctly suppressed send is flipped to "sent" -----------------
    s, se = t["send_attempt"], t["send_event"]
    sent_ts: dict[str, list] = {}
    for x in s[s.decision == "sent"].itertuples():
        sent_ts.setdefault(x.customer_id, []).append(r.parse(x.sent_at))
    cons: dict = {}
    for x in t["consent"].itertuples():
        cons.setdefault((x.customer_id, x.channel), []).append((x.effective_from, x.status))
    tz = dict(zip(t["customer"].customer_id, t["customer"].timezone_offset_h))
    cand = s[s.decision == "suppressed_no_consent"]
    cand = cand.iloc[rng.permutation(len(cand))]
    chosen = []
    for x in cand.itertuples():
        if len(chosen) >= n["message_after_optout"]:
            break
        te = r.parse(x.decision_ts) + lat
        history = cons[(x.customer_id, x.channel)]
        prior = [(ts, st) for ts, st in history if ts <= r.iso(te)]
        # Only label a true withdrawal: an earlier opt-in followed by a current opt-out.
        if not any(st == "opt_in" for _, st in prior) or not prior or prior[-1][1] != "opt_out":
            continue
        if x.channel == "sms" and r.in_quiet_hours(te, tz[x.customer_id], q["start"], q["end"]):
            continue
        if te + timedelta(seconds=30) > end or any(abs(o - te) < timedelta(days=8) for o in sent_ts.get(x.customer_id, [])):
            continue
        sent_ts.setdefault(x.customer_id, []).append(te)
        chosen.append((x.Index, te, x.send_id))
    if len(chosen) < n["message_after_optout"]:
        raise ValueError(
            "Not enough genuine consent-withdrawal candidates to inject the configured "
            f"message-after-opt-out anomalies: need {n['message_after_optout']}, found {len(chosen)}."
        )
    new_ev = []
    for idx, te, sid in chosen:
        s.loc[idx, ["decision", "sent_at", "status"]] = ["sent", r.iso(te), "delivered"]
        new_ev.append({"event_id": f"EVT-{len(se) + len(new_ev) + 1:07d}", "send_id": sid,
                       "event_type": "delivered", "event_ts": r.iso(te + timedelta(seconds=30))})
        mark("message_after_optout", "send_attempt", sid, "suppressed_no_consent", "sent although consent status was opt_out")
    t["send_event"] = pd.concat([se, pd.DataFrame(new_ev, columns=se.columns)], ignore_index=True)

    # 2 ── cart considered abandoned after purchase: time-rule abandonment ignores the earlier purchase ---
    cart, ev = t["cart"], t["cart_event"]
    pos = pd.Series(cart.index, index=cart.cart_id)
    ok = cart[(cart.status == "purchased")]
    ok = ok[[r.abandonment_time(r.parse(x), win) <= end for x in ok.last_activity_at]]
    ok = ok.iloc[rng.permutation(len(ok))][: n["abandoned_after_purchase"]]
    rows = []
    for x in ok.itertuples():
        ab = r.iso(r.abandonment_time(r.parse(x.last_activity_at), win))
        cart.loc[x.Index, ["status", "abandoned_at"]] = ["abandoned", ab]
        rows.append({"cart_event_id": f"CEV-{len(ev) + len(rows) + 1:07d}", "cart_id": x.cart_id, "session_id": x.session_id,
                     "event_type": "abandoned", "event_ts": ab, "cart_total_after": x.total_amount})
        used_carts.add(x.cart_id)
        mark("abandoned_after_purchase", "cart", x.cart_id, "not_abandoned", "purchase precedes abandonment timestamp")
    t["cart_event"] = ev = pd.concat([ev, pd.DataFrame(rows, columns=ev.columns)], ignore_index=True)

    # 3 ── attribution double-counted across campaigns: duplicate a REAL click touch
    # from another campaign by the same customer. Orders may legitimately be
    # attributed across carts for the same customer, but only once per order.
    a, o = t["attribution"], t["order"].set_index("order_id")
    ss = t["send_attempt"]
    send_meta = ss[ss.decision == "sent"][["send_id", "campaign_id", "customer_id"]]
    click_events = t["send_event"]
    click_events = click_events[click_events.event_type == "clicked"][["send_id", "event_ts"]]
    clicked_sends = send_meta.merge(click_events, on="send_id", how="inner")
    clicked_sends = clicked_sends.assign(click_ts=pd.to_datetime(clicked_sends.event_ts))
    by_customer = {k: g for k, g in clicked_sends.groupby("customer_id")}
    credited_send = ss.set_index("send_id")
    cands = []
    for x in a.itertuples():
        customer_id = credited_send.at[x.send_id, "customer_id"]
        g = by_customer.get(customer_id)
        ots = pd.Timestamp(o.at[x.order_id, "ordered_at"])
        if g is None:
            continue
        # Keep the injected exception isolated to duplicate credit. The second
        # click is real and precedes the order; it may be outside the normal
        # attribution window, but duplicate-order validation is the targeted rule.
        eligible = g[(g.send_id != x.send_id) & (g.campaign_id != x.campaign_id)
                     & (g.click_ts <= ots)]
        if not eligible.empty:
            y = eligible.sort_values(["click_ts", "send_id"], ascending=[False, True]).iloc[0]
            cands.append((x.order_id, y))
    # Require the configured count; never fabricate a click timestamp to fill a quota.
    if len(cands) < n["attribution_double_count"]:
        raise ValueError(
            "Not enough real cross-campaign click touchpoints to inject the configured "
            f"attribution anomalies: need {n['attribution_double_count']}, found {len(cands)}."
        )
    pick = rng.permutation(len(cands))[: n["attribution_double_count"]]
    rows = []
    for i in sorted(pick):
        oid, y = cands[i]
        lag = int((pd.Timestamp(o.at[oid, "ordered_at"]) - y.click_ts).total_seconds() // 60)
        rows.append({"attribution_id": r.att_id(len(a) + len(rows) + 1, yr), "order_id": oid,
                     "send_id": y.send_id, "campaign_id": y.campaign_id, "touch_type": "click",
                     "touch_ts": r.iso(y.click_ts.to_pydatetime()), "lag_minutes": lag, "credit": 1.0})
        mark("attribution_double_count", "order", oid, "single_credit",
             "order credited to two campaigns using two real click events (credit sum = 2)")
    t["attribution"] = pd.concat([a, pd.DataFrame(rows, columns=a.columns)], ignore_index=True)

    # 4 ── cart total stale after a quantity update (cart snapshot not refreshed) -----------------------
    ev = ev.sort_values("cart_event_id")
    ev = ev.assign(prev=ev.groupby("cart_id").cart_total_after.shift())
    q4 = ev[(ev.event_type == "qty_updated") & ((ev.cart_total_after - ev.prev).abs() > 0.005)]
    q4 = q4.merge(cart[["cart_id", "status"]], on="cart_id")
    q4 = q4[q4.status.isin(["abandoned", "expired", "active"]) & ~q4.cart_id.isin(used_carts)]
    q4 = q4.iloc[rng.permutation(len(q4))][: n["stale_cart_total"]]
    for x in q4.itertuples():
        cart.loc[pos[x.cart_id], "total_amount"] = x.prev
        mark("stale_cart_total", "cart", x.cart_id, "recompute_total", f"snapshot {x.prev} vs lines {x.cart_total_after}")
    t["cart"] = cart

    order = {k: i for i, k in enumerate(cfg["anomalies"])}
    gt.sort(key=lambda g: (order[g[0]], g[2]))
    truth = pd.DataFrame([(f"GT-{i + 1:04d}",) + g for i, g in enumerate(gt)], columns=list(GROUND_TRUTH_FIELDS))
    return t, truth
