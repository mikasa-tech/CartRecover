"""Tier 2: master + cart-activity generator (clean baseline; anomalies are injected later).

One seeded numpy Generator, no wall clock. Abandonment/recovery statuses are DERIVED by
domain.rules from timestamps; only latent shopper behaviour is random.
"""
from __future__ import annotations

import argparse
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from cartrecovery.config import load_config
from cartrecovery.domain import rules as r
from cartrecovery.domain.dictionary import ENTITIES

FIRST = ["Ava", "Liam", "Noah", "Emma", "Mia", "Arjun", "Priya", "Wei", "Sofia", "Omar",
         "Chloe", "Ethan", "Zara", "Ravi", "Lena", "Mateo", "Aisha", "Kenji", "Nina", "Leo"]
LAST = ["Smith", "Patel", "Kim", "Garcia", "Chen", "Khan", "Brown", "Lopez", "Singh", "Nguyen",
        "Miller", "Rossi", "Silva", "Ito", "Cohen", "Okafor", "Novak", "Reyes", "Das", "Weber"]
CATS = ["Apparel", "Footwear", "Electronics", "Home", "Beauty", "Sports", "Toys", "Books"]
CHANNELS = ["email", "sms"]
HOUR_W = np.array([1, 1, 1, 1, 1, 2, 3, 4, 5, 5, 5, 5, 5, 5, 5, 5, 6, 7, 8, 9, 9, 8, 5, 2], float)
HOUR_W /= HOUR_W.sum()


def _idx(u: float, cuts: list[float]) -> int:
    for i, c in enumerate(cuts):
        if u < c:
            return i
    return len(cuts)


def _df(name: str, rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=[f[0] for f in ENTITIES[name]["fields"]])


def _customers(cfg, rng, n):
    start = r.parse(cfg["sim_start"])
    seg = rng.choice(["new", "returning", "vip"], n, p=[0.5, 0.4, 0.1])
    tz = rng.choice([-8, -5, 0, 1, 5, 8, 10], n, p=[0.2, 0.25, 0.15, 0.15, 0.1, 0.1, 0.05])
    created = [start - timedelta(days=int(d), seconds=int(s))
               for d, s in zip(rng.integers(1, 400, n), rng.integers(0, 86400, n))]
    fi, li = rng.integers(0, len(FIRST), n), rng.integers(0, len(LAST), n)
    ph, has_ph = rng.integers(0, 10**7, n), rng.random(n) < 0.9
    nd = rng.choice([1, 2, 3], n, p=[0.6, 0.3, 0.1])
    dt = rng.choice(["mobile", "desktop", "tablet"], (n, 3), p=[0.6, 0.35, 0.05])
    rows, devices = [], []
    for i in range(n):
        f, l = FIRST[fi[i]], LAST[li[i]]
        rows.append({"customer_id": r.cus_id(i + 1), "email": f"{f}.{l}{i + 1}@example.com".lower(),
                     "phone": f"+1555{ph[i]:07d}" if has_ph[i] else None, "full_name": f"{f} {l}",
                     "segment": str(seg[i]), "timezone_offset_h": int(tz[i]), "created_at": r.iso(created[i])})
        devices.append([(f"DEV-{i + 1:06d}-{k + 1}", str(dt[i, k])) for k in range(int(nd[i]))])
    return rows, created, devices, has_ph


def _consent(cfg, rng, n, created, has_ph):
    b, start, end = cfg["behavior"]["consent"], r.parse(cfg["sim_start"]), r.parse(cfg["sim_end"])
    span = int((end - start).total_seconds())
    U = rng.random((2, 6, n))
    ev = []
    for ci in range(n):
        for k, ch in enumerate(CHANNELS):
            ok = bool(has_ph[ci]) or ch == "email"
            init = ok and U[k, 0, ci] < b["opt_in_initial"][ch]
            ev.append((ci, k, created[ci], "opt_in" if init else "opt_out", "checkout"))
            if init and U[k, 1, ci] < b["withdraw"][ch]:
                w = start + timedelta(seconds=int(U[k, 2, ci] * span))
                src = (["unsubscribe_link", "preference_center"] if ch == "email" else ["stop_reply", "admin"])
                ev.append((ci, k, w, "opt_out", src[0] if U[k, 5, ci] < 0.85 else src[1]))
                if U[k, 3, ci] < b["rejoin"]:
                    rj = w + timedelta(days=1 + int(U[k, 4, ci] * 60))
                    if rj <= end:
                        ev.append((ci, k, rj, "opt_in", "preference_center"))
            elif ok and not init and U[k, 1, ci] < b["late_opt_in"]:
                ev.append((ci, k, start + timedelta(seconds=int(U[k, 2, ci] * span)), "opt_in", "preference_center"))
    ev.sort(key=lambda e: (e[0], e[1], e[2]))
    return [{"consent_event_id": f"CON-{j + 1:07d}", "customer_id": r.cus_id(c + 1), "channel": CHANNELS[k],
             "status": s, "source": src, "effective_from": r.iso(t)} for j, (c, k, t, s, src) in enumerate(ev)]


def generate_masters(cfg: dict, scale: float = 1.0, seed: int | None = None) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(cfg["seed"] if seed is None else seed)
    sc, rl, bh = cfg["scale"], cfg["rules"], cfg["behavior"]
    start, end, year = r.parse(cfg["sim_start"]), r.parse(cfg["sim_end"]), cfg["year"]
    span_days = (end - start).days + 1
    n_c, n_cart = max(20, round(sc["customers"] * scale)), max(20, round(sc["carts"] * scale))
    total = n_cart + round(n_cart * bh["browse_only_session_share"])

    cust_rows, created, devices, has_ph = _customers(cfg, rng, n_c)
    consent_rows = _consent(cfg, rng, n_c, created, has_ph)

    # product catalog (static reference data inside the generator)
    n_sku = 300
    price = np.round(np.clip(np.exp(rng.normal(3.6, 0.8, n_sku)), 4, 600), 2)
    cat = rng.integers(0, len(CATS), n_sku)

    cidx = (n_c * rng.random(total) ** 1.6).astype(int)           # heavy repeat shoppers
    days, hrs = rng.integers(0, span_days, total), rng.choice(24, total, p=HOUR_W)
    secs = rng.integers(0, 3600, total)
    starts = [start + timedelta(days=int(d), hours=int(h), seconds=int(s)) for d, h, s in zip(days, hrs, secs)]

    sessions, carts = [], []          # temp keys; real IDs assigned after chronological sort

    def new_session(ci, ts, ended, dev=None):
        dv = devices[ci]
        d = dv[int(rng.integers(0, len(dv)))] if dev is None else dev
        sessions.append({"key": len(sessions), "ci": ci, "dev": d, "start": ts, "end": ended})
        return len(sessions) - 1

    for i in range(total):
        ci, t0 = int(cidx[i]), starts[i]
        if i >= n_cart:                                            # browse-only session, no cart
            new_session(ci, t0, t0 + timedelta(minutes=int(rng.integers(1, 30))))
            continue
        ts = t0 + timedelta(minutes=1)
        ev, lines, tot = [("created", ts, 0.0, None, None, None)], [], 0.0
        for _ in range(1 + _idx(rng.random(), [0.45, 0.73, 0.88, 0.96])):
            ts += timedelta(minutes=int(rng.integers(1, 7)), seconds=int(rng.integers(0, 60)))
            while True:
                k = int(rng.integers(0, n_sku))
                if all(l["k"] != k for l in lines):
                    break
            lines.append({"k": k, "q": 1 + _idx(rng.random(), [0.7, 0.9]), "t": ts})
            line_idx = len(lines) - 1
            tot = r.cart_total(r.line_total(float(price[l["k"]]), l["q"]) for l in lines)
            ev.append(("line_added", ts, tot, line_idx, 0, lines[line_idx]["q"]))
        if rng.random() < bh["qty_update_prob"]:
            line_idx = int(rng.integers(0, len(lines)))
            l = lines[line_idx]
            qty_before = l["q"]
            l["q"] = l["q"] + 1 if (rng.random() < 0.7 or l["q"] == 1) else l["q"] - 1
            ts += timedelta(minutes=int(rng.integers(1, 11)))
            tot = r.cart_total(r.line_total(float(price[x["k"]]), x["q"]) for x in lines)
            ev.append(("qty_updated", ts, tot, line_idx, qty_before, l["q"]))
        last = ts
        pur = None
        if rng.random() < bh["purchase_prob"]:
            if rng.random() < bh["late_return_share"]:
                pur = last + timedelta(minutes=int(rng.integers(60, 181)))
            else:
                pur = last + timedelta(minutes=int(rng.integers(3, 56)), seconds=int(rng.integers(0, 60)))
            if pur > end:
                pur = None
        aband = r.is_abandoned(last, end, rl["abandonment_window_minutes"], pur)
        ab_ts = r.abandonment_time(last, rl["abandonment_window_minutes"])
        if aband:
            ev.append(("abandoned", ab_ts, tot, None, None, None))
        status = "abandoned" if aband else "active"
        s_main = new_session(ci, t0, (pur + timedelta(minutes=1)) if (pur and not aband)
                             else last + timedelta(minutes=int(rng.integers(0, 11))))
        s_pur = s_main
        if pur:
            if aband:                                              # late return, often on another device
                s_pur = new_session(ci, pur - timedelta(minutes=int(rng.integers(2, 10))),
                                    pur + timedelta(minutes=1))
            ev.append(("recovered" if aband else "purchased", pur, tot, None, None, None))
            status = "recovered" if aband else "purchased"
        carts.append({"tmp": i, "ci": ci, "s_main": s_main, "s_pur": s_pur, "created": t0 + timedelta(minutes=1),
                      "last": last, "ab": ab_ts if aband else None, "status": status, "ev": ev,
                      "lines": lines, "tot": tot})

    order = sorted(sessions, key=lambda s: (s["start"], s["ci"], s["key"]))
    sid = {s["key"]: f"SES-{j + 1:07d}" for j, s in enumerate(order)}
    sess_rows = [{"session_id": sid[s["key"]], "customer_id": r.cus_id(s["ci"] + 1), "device_id": s["dev"][0],
                  "device_type": s["dev"][1], "started_at": r.iso(s["start"]), "ended_at": r.iso(s["end"])}
                 for s in order]

    carts.sort(key=lambda c: (c["created"], c["tmp"]))
    cart_rows, ev_rows, line_rows = [], [], []
    for j, c in enumerate(carts):
        cid = r.cart_id(j + 1, year)
        cust = r.cus_id(c["ci"] + 1)
        cart_rows.append({"cart_id": cid, "customer_id": cust, "session_id": sid[c["s_main"]], "status": c["status"],
                          "created_at": r.iso(c["created"]), "last_activity_at": r.iso(c["last"]),
                          "abandoned_at": r.iso(c["ab"]) if c["ab"] else None,
                          "item_count": sum(l["q"] for l in c["lines"]), "total_amount": c["tot"]})
        line_id_map = {idx: f"LINE-{len(line_rows) + idx + 1:07d}" for idx in range(len(c["lines"]))}
        for t, ts, tot, line_idx, qty_before, qty_after in c["ev"]:
            sess = sid[c["s_pur"]] if t in ("recovered", "purchased") else sid[c["s_main"]]
            ev_rows.append({"cart_event_id": f"CEV-{len(ev_rows) + 1:07d}", "cart_id": cid, "session_id": sess,
                            "event_type": t, "event_ts": r.iso(ts), "cart_total_after": tot,
                            "line_id": line_id_map.get(line_idx), "quantity_before": qty_before,
                            "quantity_after": qty_after})
        for l in c["lines"]:
            k = l["k"]
            line_rows.append({"line_id": f"LINE-{len(line_rows) + 1:07d}", "cart_id": cid, "sku": f"SKU-{k + 1:04d}",
                              "product_name": f"{CATS[cat[k]]} item {k + 1}", "category": CATS[cat[k]],
                              "unit_price": float(price[k]), "quantity": l["q"],
                              "line_total": r.line_total(float(price[k]), l["q"]), "added_at": r.iso(l["t"])})
    return {"customer": _df("customer", cust_rows), "consent": _df("consent", consent_rows),
            "session": _df("session", sess_rows), "cart": _df("cart", cart_rows),
            "cart_event": _df("cart_event", ev_rows), "cart_line": _df("cart_line", line_rows)}


def main() -> None:
    ap = argparse.ArgumentParser(description="CartRevive master/cart generator")
    ap.add_argument("--scale", type=float, default=0.02)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--out", default="output/starter")
    a = ap.parse_args()
    t = generate_masters(load_config(), a.scale, a.seed)
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    for n, df in t.items():
        df.to_csv(out / f"{n}.csv", index=False)
        print(f"{n:12s} {len(df):>8,}")
    c = t["cart"]
    print("abandoned_at set:", c["abandoned_at"].notna().sum(), "of", len(c))
    print(c["status"].value_counts().to_dict())


if __name__ == "__main__":
    main()
