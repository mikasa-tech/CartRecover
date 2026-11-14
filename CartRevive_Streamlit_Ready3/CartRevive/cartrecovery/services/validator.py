"""Tier 2: validation suite. Every check is a function returning violations; a failure stops the build.

Only the 4 required edge cases carry an issue_type. Anything else the checks find is "unexplained".
This module never sees the labelled truth: comparison happens in compare().
"""
from __future__ import annotations

import re
from datetime import timedelta

import pandas as pd

from cartrecovery.domain import rules as r
from cartrecovery.domain.dictionary import ENTITIES, ENUMS

COLS = ["check", "issue_type", "entity", "entity_id", "detail"]
LIFE = ("abandoned", "purchased", "recovered", "expired")
OK_SEQ = {("purchased",), ("abandoned",), ("abandoned", "recovered"), ("abandoned", "expired")}
ENUM = {f"enum {k}": v for k, v in ENUMS.items()}
T = pd.to_datetime


def _v(check, entity, ids, detail="", issue=None) -> pd.DataFrame:
    ids = [str(i) for i in ids]
    frame = pd.DataFrame({"check": check, "issue_type": issue, "entity": entity, "entity_id": ids, "detail": detail},
                         columns=COLS)
    frame.attrs["check_name"] = check
    frame.attrs["entity_name"] = entity
    return frame


def _rid(df, pk):
    cols = pk.split(",")
    if df.empty:
        return pd.Series([], dtype=str)
    return df[cols].astype(str).agg("|".join, axis=1) if len(cols) > 1 else df[cols[0]]


def check_structure(t, cfg):
    out = []
    for name, e in ENTITIES.items():
        df, pk = t[name], e["pk"]
        out.append(_v("pk_unique", name, _rid(df[df.duplicated(pk.split(","), keep=False)], pk)))
        for f, _typ, req, allowed, _u, uniq in e["fields"]:
            if req:
                out.append(_v("required_null", name, _rid(df[df[f].isna()], pk), f))
            if uniq and f != pk:
                nn = df[df[f].notna()]
                out.append(_v("unique_field", name, _rid(nn[nn.duplicated(f, keep=False)], pk), f))
            if allowed in ENUM:
                out.append(_v("enum", name, _rid(df[df[f].notna() & ~df[f].isin(ENUM[allowed])], pk), f))
            elif "\\d" in allowed and re.match(r"^[A-Z]+-", allowed):
                rx = re.compile(allowed.replace("<cart_id>", r"CART-2026-\d{6}"))
                nn = df[df[f].notna()]
                out.append(_v("id_format", name, _rid(nn[~nn[f].astype(str).map(lambda s: bool(rx.fullmatch(s)))], pk), f))
        for col, ref in e["fks"].items():
            pt, pc = ref.split(".")
            nn = df[df[col].notna()]
            out.append(_v("fk_integrity", name, _rid(nn[~nn[col].isin(t[pt][pc])], pk), f"{col}->{ref}"))
    s = t["send_attempt"]
    out.append(_v("sent_at_iff_sent", "send_attempt", s[(s.decision == "sent") != s.sent_at.notna()].send_id))
    return out


def check_dates(t, cfg):
    out = []
    se, c, s = t["session"], t["cart"], t["send_attempt"]
    out.append(_v("session_dates", "session", se[T(se.ended_at) < T(se.started_at)].session_id))
    out.append(_v("cart_dates", "cart", c[T(c.last_activity_at) < T(c.created_at)].cart_id))
    cm = t["campaign"]
    out.append(_v("campaign_dates", "campaign", cm[T(cm.ends_at) <= T(cm.starts_at)].campaign_id))
    sent = s[s.sent_at.notna()]
    out.append(_v("sent_before_decision", "send_attempt", sent[T(sent.sent_at) < T(sent.decision_ts)].send_id))
    ab = s.merge(c[["cart_id", "abandoned_at"]], on="cart_id")
    out.append(_v("decision_before_abandon", "send_attempt", ab[T(ab.decision_ts) < T(ab.abandoned_at)].send_id))
    ev = t["send_event"].merge(s[["send_id", "sent_at"]], on="send_id")
    out.append(_v("event_before_send", "send_event", ev[T(ev.event_ts) < T(ev.sent_at)].event_id))
    a = t["attribution"].merge(t["order"][["order_id", "ordered_at"]], on="order_id")
    out.append(_v("touch_after_order", "attribution", a[T(a.touch_ts) > T(a.ordered_at)].attribution_id))
    lag = ((T(a.ordered_at) - T(a.touch_ts)).dt.total_seconds() // 60).astype(int)
    out.append(_v("lag_minutes", "attribution", a[lag != a.lag_minutes].attribution_id))
    k = t["consent"].assign(ts=T(t["consent"].effective_from))
    out.append(_v("consent_ts_unique", "consent", k[k.duplicated(["customer_id", "channel", "ts"], keep=False)].consent_event_id))
    return out


def check_cart_event_details(t, cfg):
    """Validate reconstructable line mutations and append-only per-cart event order."""
    ev, ln = t["cart_event"], t["cart_line"]
    out = []
    line_events = ev[ev.event_type.isin(["line_added", "qty_updated", "line_removed"])].copy()
    missing = line_events[line_events[["line_id", "quantity_before", "quantity_after"]].isna().any(axis=1)]
    out.append(_v("line_event_required_detail", "cart_event", missing.cart_event_id))

    added = line_events[line_events.event_type == "line_added"]
    out.append(_v("line_added_quantity_delta", "cart_event",
                  added[(added.quantity_before != 0) | (added.quantity_after < 1)].cart_event_id))
    updated = line_events[line_events.event_type == "qty_updated"]
    out.append(_v("qty_updated_quantity_delta", "cart_event",
                  updated[updated.quantity_before.isna() | updated.quantity_after.isna()
                          | (updated.quantity_before < 1) | (updated.quantity_after < 1)
                          | (updated.quantity_before == updated.quantity_after)].cart_event_id))
    removed = line_events[line_events.event_type == "line_removed"]
    out.append(_v("line_removed_quantity_delta", "cart_event",
                  removed[removed.quantity_before.isna() | (removed.quantity_before < 1)
                          | (removed.quantity_after.fillna(-1) != 0)].cart_event_id))

    linked = line_events[line_events.line_id.notna()].merge(
        ln[["line_id", "cart_id"]], on="line_id", how="left", suffixes=("_event", "_line"))
    out.append(_v("line_event_cart_match", "cart_event",
                  linked[linked.cart_id_event != linked.cart_id_line].cart_event_id))

    # The event ledger is append-only: event IDs increase in the same order as
    # event timestamps for each cart, including deliberately injected anomalies.
    ordered = ev.sort_values("cart_event_id")
    ts = pd.to_datetime(ordered.event_ts)
    prev = ts.groupby(ordered.cart_id).shift()
    out.append(_v("cart_event_chronology", "cart_event",
                  ordered[ts < prev].cart_event_id))

    # Latest recorded quantity for each line must agree with the current line snapshot.
    qty_events = line_events[line_events.quantity_after.notna()].sort_values("cart_event_id")
    if not qty_events.empty:
        latest = qty_events.drop_duplicates("line_id", keep="last").set_index("line_id").quantity_after
        snap = ln.set_index("line_id").quantity
        common = latest.index.intersection(snap.index)
        mismatch = latest.loc[common] != snap.loc[common]
        out.append(_v("line_quantity_matches_event_history", "cart_line", common[mismatch]))
    else:
        out.append(_v("line_quantity_matches_event_history", "cart_line", []))
    return out


def check_arithmetic(t, cfg):
    out, ln, c = [], t["cart_line"], t["cart"]
    out.append(_v("line_total", "cart_line", ln[(ln.line_total - (ln.unit_price * ln.quantity).round(2)).abs() > 0.005].line_id))
    g = ln.groupby("cart_id").agg(tot=("line_total", "sum"), q=("quantity", "sum"))
    m = c.merge(g, left_on="cart_id", right_index=True, how="left")
    out.append(_v("cart_total_vs_lines", "cart", m[(m.total_amount - m.tot).abs() > 0.005].cart_id,
                  "cart total != sum of lines", "stale_cart_total"))
    out.append(_v("item_count", "cart", m[m.item_count != m.q].cart_id))
    o = t["order"].merge(c[["cart_id", "total_amount"]], on="cart_id", suffixes=("", "_cart"))
    out.append(_v("order_total_vs_cart", "order", o[(o.total_amount - o.total_amount_cart).abs() > 0.005].order_id))
    return out


def _lifecycle(t):
    ev = t["cart_event"]
    life = ev[ev.event_type.isin(LIFE)].assign(ts=T(ev.event_ts))
    return life.sort_values(["cart_id", "ts", "cart_event_id"]).groupby("cart_id").event_type.agg(tuple)


def check_lifecycle(t, cfg):
    seq, out = _lifecycle(t), []
    bad = seq[~seq.isin(OK_SEQ)]
    after = bad[bad.map(lambda s: s[0] in ("purchased", "recovered") and "abandoned" in s[1:])]
    out.append(_v("abandoned_after_purchase", "cart", after.index, "abandoned event follows purchase",
                  "abandoned_after_purchase"))
    out.append(_v("lifecycle_transition", "cart", bad.drop(after.index).index))
    exp = seq.map(lambda s: s[-1]).reindex(t["cart"].cart_id).fillna("active")
    cur = t["cart"].set_index("cart_id").status
    out.append(_v("cart_status_vs_events", "cart", cur[cur != exp.values].index))
    return out


def check_abandonment(t, cfg):
    w, end = cfg["rules"]["abandonment_window_minutes"], r.parse(cfg["sim_end"])
    c, ev = t["cart"], t["cart_event"]
    skip = set(_lifecycle_after_purchase(t))
    last = T(c.last_activity_at)
    t_ab = last + pd.Timedelta(minutes=w)
    pur = ev[ev.event_type.isin(["purchased", "recovered"])].assign(ts=T(ev.event_ts)).groupby("cart_id").ts.min()
    p = c.cart_id.map(pur)
    expected = (t_ab <= pd.Timestamp(end)) & ~(p <= t_ab)
    abn = ev[ev.event_type == "abandoned"].assign(ts=T(ev.event_ts)).groupby("cart_id").ts.min()
    has = c.cart_id.isin(abn.index)
    ab_at = T(c.abandoned_at)
    fire = ~c.cart_id.isin(skip)
    out = [_v("abandonment_completeness", "cart", c[fire & (has != expected)].cart_id, "time rule vs events"),
           _v("abandoned_at_matches_rule", "cart", c[has & fire & (ab_at != t_ab)].cart_id),
           _v("abandoned_at_without_event", "cart", c[~has & c.abandoned_at.notna()].cart_id),
           _v("abandon_event_ts", "cart", c[has & fire & (c.cart_id.map(abn) != t_ab)].cart_id)]
    return out


def _lifecycle_after_purchase(t):
    seq = _lifecycle(t)
    bad = seq[~seq.isin(OK_SEQ)]
    return bad[bad.map(lambda s: s[0] in ("purchased", "recovered") and "abandoned" in s[1:])].index


def _consent_at(t, df, col):
    c = t["consent"].assign(eff=T(t["consent"].effective_from)).sort_values("eff")
    left = df.assign(_ts=T(df[col])).sort_values("_ts")
    m = pd.merge_asof(left, c[["customer_id", "channel", "eff", "status"]].rename(columns={"status": "cst"}),
                      left_on="_ts", right_on="eff", by=["customer_id", "channel"], direction="backward")
    m.index = left.index
    return m["cst"]


def check_consent_and_caps(t, cfg):
    rl, lat = cfg["rules"], timedelta(minutes=cfg["campaign"]["dispatch_latency_minutes"])
    s, out = t["send_attempt"], []
    sent = s[s.decision == "sent"]
    st = _consent_at(t, sent, "sent_at")
    out.append(_v("consent_at_send", "send_attempt", sent[st.reindex(sent.index) != "opt_in"].send_id,
                  "sent without opt_in", "message_after_optout"))
    sup = s[s.decision == "suppressed_no_consent"].copy()
    sup["te"] = T(sup.decision_ts) + lat
    out.append(_v("suppression_consent", "send_attempt", sup[_consent_at(t, sup, "te").reindex(sup.index) == "opt_in"].send_id))
    ev = t["cart_event"]
    pur = ev[ev.event_type.isin(["purchased", "recovered"])].assign(ts=T(ev.event_ts)).groupby("cart_id").ts.min()
    x = sent.assign(ts=T(sent.sent_at), p=sent.cart_id.map(pur).values)
    out.append(_v("send_after_purchase", "send_attempt", x[x.p <= x.ts].send_id))
    sp = s[s.decision == "suppressed_purchased"]
    xp = sp.assign(te=T(sp.decision_ts) + lat, p=sp.cart_id.map(pur).values)
    out.append(_v("suppression_purchased", "send_attempt", xp[~(xp.p <= xp.te)].send_id))
    cap, days, bad = rl["frequency_cap"]["max_sends"], pd.Timedelta(days=rl["frequency_cap"]["per_days"]), []
    for _, g in x.sort_values(["customer_id", "ts"]).groupby("customer_id"):
        ts, ids, lo = list(g.ts), list(g.send_id), 0
        for i, v in enumerate(ts):
            while ts[lo] <= v - days:
                lo += 1
            if i - lo + 1 > cap:
                bad.append(ids[i])
    out.append(_v("frequency_cap", "send_attempt", bad))
    tz = t["customer"].set_index("customer_id").timezone_offset_h
    h = (x.ts + pd.to_timedelta(x.customer_id.map(tz), unit="h")).dt.hour
    q = rl["quiet_hours"]
    out.append(_v("quiet_hours_sms", "send_attempt", x[(x.channel == "sms") & ((h >= q["start"]) | (h < q["end"]))].send_id))
    cm = t["campaign"].set_index("campaign_id")
    d = T(s.decision_ts)
    ok = (d >= T(s.campaign_id.map(cm.starts_at))) & (d <= T(s.campaign_id.map(cm.ends_at)))
    out.append(_v("send_in_campaign_window", "send_attempt", s[~ok].send_id))
    return out


def check_attribution(t, cfg):
    win = pd.Timedelta(days=cfg["rules"]["attribution_window_days"])
    a, o, s = t["attribution"], t["order"], t["send_attempt"].set_index("send_id")
    cnt = a.groupby("order_id").size()
    multi = cnt[cnt > 1].index
    out = [_v("attribution_credit_once", "order", multi, "order credited more than once",
              "attribution_double_count"),
           _v("attribution_campaign_consistent", "attribution", a[a.campaign_id != a.send_id.map(s.campaign_id)].attribution_id)]
    lag_bad = a[(a.lag_minutes > win.total_seconds() / 60) & ~a.order_id.isin(multi)]
    out.append(_v("attribution_window", "attribution", lag_bad.attribution_id))
    cl = t["send_event"][t["send_event"].event_type == "clicked"][["send_id", "event_ts"]]
    cl = cl.assign(cts=T(cl.event_ts), customer_id=cl.send_id.map(s.customer_id))
    m = o[["order_id", "customer_id", "ordered_at"]].assign(ots=T(o.ordered_at)).merge(cl, on="customer_id")
    m["lag"] = m.ots - m.cts
    m = m[(m.lag >= pd.Timedelta(0)) & (m.lag <= win)]
    exp = m.sort_values(["order_id", "cts", "send_id"], ascending=[True, False, True]).drop_duplicates("order_id")
    exp = exp.set_index("order_id").send_id
    exp = exp[~exp.index.isin(multi)]
    rec = a[~a.order_id.isin(multi)].set_index("order_id").send_id
    both = exp.index.intersection(rec.index)
    diff = set(exp.index) ^ set(rec.index) | {i for i in both if exp[i] != rec[i]}
    out.append(_v("attribution_deterministic", "order", sorted(diff), "last-click recompute differs"))
    return out


def check_reconcile(t, cfg):
    ev, o, c, a = t["cart_event"], t["order"], t["cart"], t["attribution"]
    out = []
    pc = set(ev[ev.event_type.isin(["purchased", "recovered"])].cart_id)
    out.append(_v("orders_match_purchase_events", "cart", sorted(pc ^ set(o.cart_id.dropna()))))
    out.append(_v("duplicate_order_per_cart", "order", o[o.duplicated("cart_id", keep=False)].order_id))
    out.append(_v("duplicate_attribution_pair", "attribution", a[a.duplicated(["order_id", "send_id"], keep=False)].attribution_id))
    out.append(_v("duplicate_message", "send_attempt", t["send_attempt"][t["send_attempt"].duplicated("message_id", keep=False)].send_id))
    ok = a[~a.order_id.duplicated(keep=False)].merge(o[["order_id", "total_amount"]], on="order_id")
    by_campaign = (ok.credit * ok.total_amount).groupby(ok.campaign_id).sum().sum()
    if abs(by_campaign - ok.drop_duplicates("order_id").total_amount.sum()) > 0.01:
        out.append(_v("attributed_revenue_reconciles", "attribution", ["ALL"]))
    out.append(_v("attributed_le_orders", "attribution", [] if a.order_id.nunique() <= len(o) else ["ALL"]))
    return out


CHECKS = [check_structure, check_dates, check_cart_event_details, check_arithmetic, check_lifecycle,
          check_abandonment, check_consent_and_caps, check_attribution, check_reconcile]


def validate_detailed(t: dict[str, pd.DataFrame], cfg: dict) -> tuple[pd.DataFrame, dict]:
    """Run every rule once and return violations plus a machine-readable run summary.

    `runs` counts executions of each individual rule (including rules with zero
    violations); `violation_count` counts the rows emitted by that rule.
    """
    violation_parts: list[pd.DataFrame] = []
    rule_summary: list[dict] = []
    group_summary: list[dict] = []

    for group_check in CHECKS:
        frames = group_check(t, cfg)
        group_summary.append({
            "check_group": group_check.__name__,
            "runs": 1,
            "rule_checks": len(frames),
            "violation_count": int(sum(len(frame) for frame in frames)),
        })
        for frame in frames:
            check_name = frame["check"].iloc[0] if not frame.empty else frame.attrs["check_name"]
            rule_summary.append({
                "check": check_name,
                "check_group": group_check.__name__,
                "entity": frame.attrs.get("entity_name"),
                "runs": 1,
                "violation_count": int(len(frame)),
                "status": "PASS" if frame.empty else "FAIL",
            })
            if not frame.empty:
                violation_parts.append(frame)

    violations = (
        pd.concat(violation_parts, ignore_index=True)
        if violation_parts else pd.DataFrame(columns=COLS)
    )
    summary = {
        "status": "PASS" if violations.empty else "FAIL",
        "check_groups_executed": int(sum(x["runs"] for x in group_summary)),
        "rule_checks_executed": int(sum(x["runs"] for x in rule_summary)),
        "total_violations": int(len(violations)),
        "check_groups": group_summary,
        "checks": rule_summary,
    }
    return violations, summary


def validate(t: dict[str, pd.DataFrame], cfg: dict) -> pd.DataFrame:
    """Backward-compatible violations-only API."""
    return validate_detailed(t, cfg)[0]


def compare(viol: pd.DataFrame, truth: pd.DataFrame) -> dict:
    """truth = the separately stored label table; only used here, after validation has run."""
    det = viol[viol.issue_type.notna()]
    d = set(zip(det.issue_type, det.entity_id))
    g = set(zip(truth.issue_type, truth.entity_id))
    unexplained = viol[viol.issue_type.isna()]
    return {"detected": len(d), "labelled": len(g), "missed": sorted(g - d), "false_positive": sorted(d - g),
            "unexplained": unexplained, "passed": d == g and unexplained.empty}
