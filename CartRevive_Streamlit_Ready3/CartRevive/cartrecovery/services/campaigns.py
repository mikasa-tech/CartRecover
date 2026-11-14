"""Tier 2: campaigns, send decisions (consent / cap / quiet hours), delivery events, orders, attribution.

Event-driven and chronological: a heap of (decision_ts, cart, seq) is processed in order so the
frequency cap and consent are always evaluated against history that already exists.
Never imports ground-truth code.
"""
from __future__ import annotations

import heapq
from datetime import timedelta

import numpy as np
import pandas as pd

from cartrecovery.domain import rules as r
from cartrecovery.services.generator import CHANNELS, _df, _idx

BODY = {
    "reminder": ("You left something behind", "Hi {first_name}, your cart ({cart_total}) is waiting.", 0),
    "discount_10": ("10% off your cart", "Hi {first_name}, take 10% off your {cart_total} cart today.", 10),
    "urgency": ("Items in your cart are selling fast", "Hi {first_name}, your {cart_total} cart may sell out soon.", 0),
    "free_ship": ("Free shipping on your cart", "Hi {first_name}, free shipping on your {cart_total} cart.", 0),
}


def _templates(variants):
    rows, tid = [], {}
    for v in variants:
        for ch in CHANNELS:
            n = len(rows) + 1
            subj, body, disc = BODY[v]
            tid[(v, ch)] = f"TPL-{n:03d}"
            rows.append({"template_id": tid[(v, ch)], "variant": v, "channel": ch,
                         "subject": subj if ch == "email" else None, "body": body, "discount_pct": disc})
    return rows, tid


def _campaigns(cfg, variants, tid):
    start, end, yr = r.parse(cfg["sim_start"]), r.parse(cfg["sim_end"]), cfg["year"]
    slices = max(1, round(cfg["scale"]["campaigns"] / (len(variants) * len(CHANNELS))))
    bounds = [start + (end - start) * i / slices for i in range(slices + 1)]
    rows, lookup = [], {}
    for i in range(slices):
        for v in variants:
            for ch in CHANNELS:
                cid = r.cmp_id(len(rows) + 1, yr)
                s, e = bounds[i], bounds[i + 1]
                rows.append({"campaign_id": cid, "name": f"{v} {ch} wave {i + 1}", "variant": v, "channel": ch,
                             "template_id": tid[(v, ch)], "starts_at": r.iso(s), "ends_at": r.iso(e),
                             "status": "running" if i == slices - 1 else "completed"})
                lookup.setdefault((v, ch), []).append((s, e, cid, i == slices - 1))
    return rows, lookup


def _campaign_for(lookup, v, ch, at):
    for s, e, cid, last in lookup[(v, ch)]:
        if s <= at < e or (last and at <= e):
            return cid
    raise ValueError(f"no campaign for {v}/{ch} at {at}")


def run_campaigns(cfg: dict, t: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng([cfg["seed"], 3])          # independent stream: chunk-2 data unchanged
    rl, cp, rs, yr = cfg["rules"], cfg["campaign"], cfg["response"], cfg["year"]
    end = r.parse(cfg["sim_end"])
    lat = timedelta(minutes=cp["dispatch_latency_minutes"])
    expiry = timedelta(days=cp["cart_expiry_days"])
    vkeys = list(cfg["variants"])
    vcum = np.cumsum([cfg["variants"][k] for k in vkeys]).tolist()[:-1]
    cap, quiet = rl["frequency_cap"], rl["quiet_hours"]

    tpl_rows, tid = _templates(vkeys)
    camp_rows, lookup = _campaigns(cfg, vkeys, tid)

    cons: dict = {}
    for x in t["consent"].itertuples():
        cons.setdefault((x.customer_id, x.channel), []).append((x.effective_from, x.status))
    tz = dict(zip(t["customer"].customer_id, t["customer"].timezone_offset_h))
    pe = t["cart_event"]
    pe = pe[pe.event_type.isin(["purchased", "recovered"])]
    org = {c: r.parse(ts) for c, ts in zip(pe.cart_id, pe.event_ts)}            # unprompted purchases

    cart = t["cart"]
    info = {x.cart_id: x for x in cart.itertuples()}
    heap = []
    for x in cart[cart.abandoned_at.notna() & (cart.total_amount >= cp["min_cart_value"])].itertuples():
        ch = "email" if rng.random() < cfg["channel_mix"]["email"] else "sms"
        v = vkeys[_idx(rng.random(), vcum)]
        dec = r.parse(x.abandoned_at) + timedelta(minutes=rl["send_delay_minutes"][v])
        if dec + lat <= end:
            heapq.heappush(heap, (dec, x.cart_id, 1, v, ch))

    sends, events, targets, bought, hist, clicks, camp_of = [], [], {}, {}, {}, {}, {}
    while heap:
        dec, cid, seq, v, ch = heapq.heappop(heap)
        c = info[cid]
        te = dec + lat
        purchased = any(p <= te for p in (org.get(cid), bought.get(cid)) if p)
        d = r.send_decision(
            purchased=purchased, consented=r.has_consent(cons.get((c.customer_id, ch), []), te),
            cap_ok=r.within_cap(hist.get(c.customer_id, []), te, cap["max_sends"], cap["per_days"]),
            quiet=r.in_quiet_hours(te, tz[c.customer_id], quiet["start"], quiet["end"]), channel=ch)
        sid, camp = r.send_id(len(sends) + 1, yr), _campaign_for(lookup, v, ch, dec)
        camp_of[sid] = camp
        targets.setdefault((camp, cid), r.iso(dec))
        row = {"send_id": sid, "message_id": r.msg_id(cid, seq), "campaign_id": camp, "cart_id": cid,
               "customer_id": c.customer_id, "channel": ch, "decision": d, "decision_ts": r.iso(dec),
               "sent_at": None, "status": "suppressed"}
        sends.append(row)
        if d != "sent":
            continue
        row["sent_at"] = r.iso(te)
        hist.setdefault(c.customer_id, []).append(te)

        def add(kind, ts):
            if ts <= end:
                events.append((sid, kind, ts))
                return True
            return False

        click = None
        if rng.random() < rs["deliver"][ch]:
            row["status"] = "delivered"
            dl = te + timedelta(seconds=int(rng.integers(5, 121)))
            add("delivered", dl)
            if ch == "email":
                if rng.random() < rs["email_open"]:
                    op = dl + timedelta(minutes=int(min(2880, max(1, rng.lognormal(3.5, 1.2)))))
                    if add("opened", op) and rng.random() < rs["email_click_given_open"]:
                        click = op + timedelta(minutes=int(rng.integers(1, 31)))
            elif rng.random() < rs["sms_click"]:
                click = dl + timedelta(minutes=int(rng.integers(1, 181)))
        else:
            row["status"] = "bounced"
            add("bounced", te + timedelta(seconds=int(rng.integers(30, 301))))
        if click is not None and add("clicked", click):
            clicks.setdefault(c.customer_id, []).append((sid, click))
            if cid not in org and cid not in bought and rng.random() < rs["click_to_purchase"][v]:
                u = rng.random()
                late_days = max(1, int(rl["late_purchase_after_days"]))
                late_min = late_days * 1440
                late_max = max(late_min + 1, 2 * late_min)
                mins = (int(rng.integers(5, 361)) if u < 0.65 else int(rng.integers(361, 8641)) if u < 0.93
                        else int(rng.integers(late_min, late_max + 1)))             # configured late-purchase band
                bt = click + timedelta(minutes=mins)
                if bt <= min(end, r.parse(c.abandoned_at) + expiry):
                    bought[cid] = bt
        if seq == 1:
            others = [k for k in vkeys if k != v]
            v2 = others[_idx(rng.random() * (1 - cfg["variants"][v]), _cum_excl(cfg["variants"], v))]
            dec2 = dec + timedelta(hours=cp["followup_after_hours"])
            if dec2 + lat <= end:
                heapq.heappush(heap, (dec2, cid, 2, v2, ch))

    # ---- cart lifecycle updates, appended as events (history preserved) ----
    sd = t["session"][["customer_id", "device_id", "device_type"]].drop_duplicates()
    devs = {k: list(zip(g.device_id, g.device_type)) for k, g in sd.groupby("customer_id")}
    new_sess, new_ev, status = [], [], {}
    n_s, n_e = len(t["session"]), len(t["cart_event"])
    for cid in sorted(bought):
        c, bt = info[cid], bought[cid]
        dv = devs[c.customer_id][int(rng.integers(0, len(devs[c.customer_id])))]
        n_s += 1
        new_sess.append({"session_id": f"SES-{n_s:07d}", "customer_id": c.customer_id, "device_id": dv[0],
                         "device_type": dv[1], "started_at": r.iso(bt - timedelta(minutes=int(rng.integers(2, 10)))),
                         "ended_at": r.iso(bt + timedelta(minutes=1))})
        n_e += 1
        new_ev.append({"cart_event_id": f"CEV-{n_e:07d}", "cart_id": cid, "session_id": f"SES-{n_s:07d}",
                       "event_type": "recovered", "event_ts": r.iso(bt), "cart_total_after": c.total_amount})
        status[cid] = "recovered"
    for x in cart[cart.abandoned_at.notna()].itertuples():
        if x.cart_id in org or x.cart_id in bought:
            continue
        ex = r.parse(x.abandoned_at) + expiry
        if ex <= end:
            n_e += 1
            new_ev.append({"cart_event_id": f"CEV-{n_e:07d}", "cart_id": x.cart_id, "session_id": x.session_id,
                           "event_type": "expired", "event_ts": r.iso(ex), "cart_total_after": x.total_amount})
            status[x.cart_id] = "expired"

    cart2 = cart.copy()
    cart2["status"] = cart2.cart_id.map(status).fillna(cart2.status)

    # ---- orders (one per purchase), then deterministic last-click attribution ----
    purchases = sorted([(ts, c) for c, ts in org.items()] + [(ts, c) for c, ts in bought.items()])
    orders, attrib = [], []
    for ts, cid in purchases:
        c, u = info[cid], rng.random()
        oid = r.ord_id(len(orders) + 1, yr)
        orders.append({"order_id": oid, "customer_id": c.customer_id, "cart_id": cid, "ordered_at": r.iso(ts),
                       "total_amount": c.total_amount,
                       "status": "cancelled" if u < rs["cancel_rate"] else
                       "refunded" if u < rs["cancel_rate"] + rs["refund_rate"] else "placed"})
        win = r.attribute(ts, clicks.get(c.customer_id, []), rl["attribution_window_days"])
        if win:
            tt = dict(clicks[c.customer_id])[win]
            attrib.append({"attribution_id": r.att_id(len(attrib) + 1, yr), "order_id": oid, "send_id": win,
                           "campaign_id": camp_of[win], "touch_type": "click", "touch_ts": r.iso(tt),
                           "lag_minutes": int((ts - tt).total_seconds() // 60), "credit": 1.0})

    events.sort(key=lambda e: (e[2], e[0], e[1]))
    ev_rows = [{"event_id": f"EVT-{i + 1:07d}", "send_id": s, "event_type": k, "event_ts": r.iso(ts)}
               for i, (s, k, ts) in enumerate(events)]
    tg = [{"campaign_id": k[0], "cart_id": k[1], "evaluated_at": v} for k, v in sorted(targets.items())]
    return {
        "session": pd.concat([t["session"], _df("session", new_sess)], ignore_index=True),
        "cart": cart2,
        "cart_event": pd.concat([t["cart_event"], _df("cart_event", new_ev)], ignore_index=True),
        "message_template": _df("message_template", tpl_rows), "campaign": _df("campaign", camp_rows),
        "campaign_target": _df("campaign_target", tg), "send_attempt": _df("send_attempt", sends),
        "send_event": _df("send_event", ev_rows), "order": _df("order", orders),
        "attribution": _df("attribution", attrib),
    }


def _cum_excl(weights: dict, drop: str) -> list[float]:
    """Cumulative cut points over variants excluding `drop` (follow-up must use a different variant)."""
    ks = [k for k in weights if k != drop]
    out, acc = [], 0.0
    for k in ks[:-1]:
        acc += weights[k]
        out.append(acc)
    return out
