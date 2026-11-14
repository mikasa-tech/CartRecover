import pandas as pd
import pytest

from cartrecovery.config import load_config
from cartrecovery.services.pipeline import build_dataset

CFG = load_config()
W = CFG["rules"]


@pytest.fixture(scope="module")
def t():
    return build_dataset(CFG, 0.05)


def test_deterministic(t):
    t2 = build_dataset(CFG, 0.05)
    for k in t:
        pd.testing.assert_frame_equal(t[k], t2[k])


def test_foreign_keys(t):
    ok = lambda c, p: t[c.split(".")[0]][c.split(".")[1]].dropna().isin(t[p.split(".")[0]][p.split(".")[1]]).all()
    for c, p in [("send_attempt.campaign_id", "campaign.campaign_id"), ("send_attempt.cart_id", "cart.cart_id"),
                 ("send_attempt.customer_id", "customer.customer_id"), ("send_event.send_id", "send_attempt.send_id"),
                 ("order.cart_id", "cart.cart_id"), ("attribution.order_id", "order.order_id"),
                 ("attribution.send_id", "send_attempt.send_id"), ("campaign.template_id", "message_template.template_id"),
                 ("cart_event.session_id", "session.session_id"), ("campaign_target.cart_id", "cart.cart_id")]:
        assert ok(c, p), c
    for k in ("send_attempt", "order", "attribution", "send_event", "cart_event", "session"):
        assert t[k].iloc[:, 0].is_unique


def test_consent_enforced_at_send(t):
    s = t["send_attempt"]
    s = s[s.decision == "sent"]
    c = t["consent"].sort_values(["effective_from", "consent_event_id"])
    for x in s.itertuples():
        h = c[(c.customer_id == x.customer_id) & (c.channel == x.channel) & (c.effective_from <= x.sent_at)]
        assert len(h) and h.iloc[-1].status == "opt_in", x.send_id


def test_frequency_cap_never_exceeded(t):
    s = t["send_attempt"]
    s = s[s.decision == "sent"].assign(ts=lambda d: pd.to_datetime(d.sent_at))
    for _, g in s.groupby("customer_id"):
        ts = list(g.ts.sort_values())
        for i, x in enumerate(ts):
            n = sum(1 for y in ts[: i + 1] if y > x - pd.Timedelta(days=W["frequency_cap"]["per_days"]))
            assert n <= W["frequency_cap"]["max_sends"]


def test_no_send_after_purchase_and_suppressed_shape(t):
    s, e = t["send_attempt"], t["cart_event"]
    p = e[e.event_type.isin(["purchased", "recovered"])].groupby("cart_id").event_ts.min()
    sent = s[s.decision == "sent"].copy()
    sent["p"] = sent.cart_id.map(p)
    assert (sent.p.isna() | (sent.p > sent.sent_at)).all()
    sup = s[s.decision != "sent"]
    assert sup.sent_at.isna().all() and (sup.status == "suppressed").all()
    assert not t["send_event"].send_id.isin(sup.send_id).any()


def test_events_after_send_and_ids(t):
    s = t["send_attempt"].set_index("send_id")
    e = t["send_event"]
    assert (e.event_ts >= e.send_id.map(s.sent_at)).all()
    assert (t["send_attempt"].message_id.str.match(r"^MSG-CART-2026-\d{6}-0[12]$")).all()
    assert t["campaign"].campaign_id.str.match(r"^CMP-2026-\d{6}$").all()


def test_attribution_deterministic_and_windowed(t):
    a = t["attribution"].merge(t["order"], on="order_id")
    assert a.order_id.is_unique                                  # baseline: one credit per order
    assert (a.lag_minutes >= 0).all() and (a.lag_minutes <= W["attribution_window_days"] * 1440).all()
    assert (a.credit == 1.0).all()
    cl = t["send_event"][t["send_event"].event_type == "clicked"].merge(t["send_attempt"], on="send_id")
    for x in a.itertuples():                                     # winner is the latest click in window
        c = cl[(cl.customer_id == x.customer_id) & (cl.event_ts <= x.ordered_at)]
        c = c[pd.to_datetime(x.ordered_at) - pd.to_datetime(c.event_ts) <= pd.Timedelta(days=7)]
        assert c.sort_values(["event_ts", "send_id"], ascending=[False, True]).iloc[0].send_id == x.send_id


def test_orders_reconcile_with_carts(t):
    ev = t["cart_event"]
    n = ev.event_type.isin(["purchased", "recovered"]).sum()
    assert len(t["order"]) == n and t["order"].cart_id.is_unique
    o = t["order"].merge(t["cart"], on="cart_id")
    assert ((o.total_amount_x - o.total_amount_y).abs() < 0.005).all()
    assert t["cart"][t["cart"].status == "recovered"].cart_id.isin(t["order"].cart_id).all()
