import pandas as pd
import pytest

from cartrecovery.config import load_config
from cartrecovery.services.generator import generate_masters

CFG = load_config()


@pytest.fixture(scope="module")
def t():
    return generate_masters(CFG, 0.02)


def test_deterministic(t):
    t2 = generate_masters(CFG, 0.02)
    for k in t:
        pd.testing.assert_frame_equal(t[k], t2[k])


def test_pk_unique_and_fk(t):
    assert t["cart"].cart_id.is_unique and t["session"].session_id.is_unique
    assert t["cart"].customer_id.isin(t["customer"].customer_id).all()
    assert t["cart"].session_id.isin(t["session"].session_id).all()
    assert t["cart_event"].cart_id.isin(t["cart"].cart_id).all()
    assert t["cart_event"].session_id.isin(t["session"].session_id).all()
    assert t["cart_line"].cart_id.isin(t["cart"].cart_id).all()
    assert t["consent"].customer_id.isin(t["customer"].customer_id).all()


def test_cart_total_reconciles(t):
    g = t["cart_line"].groupby("cart_id").agg(tot=("line_total", "sum"), q=("quantity", "sum"))
    c = t["cart"].set_index("cart_id").join(g)
    assert ((c.total_amount - c.tot).abs() < 0.005).all() and (c.item_count == c.q).all()
    last = t["cart_event"].sort_values("cart_event_id").groupby("cart_id").cart_total_after.last()
    assert ((c.total_amount - last.reindex(c.index)).abs() < 0.005).all()


def test_abandonment_is_time_based(t):
    c = t["cart"]
    a = c[c.abandoned_at.notna()]
    gap = (pd.to_datetime(a.abandoned_at) - pd.to_datetime(a.last_activity_at)).dt.total_seconds() / 60
    assert (gap == CFG["rules"]["abandonment_window_minutes"]).all()
    assert 0.30 < len(a) / len(c) < 0.50                      # ~40% abandoned emerges from rules


def test_event_order_and_statuses(t):
    e = t["cart_event"]
    assert (e.groupby("cart_id").event_ts.apply(lambda s: s.is_monotonic_increasing)).all()
    assert set(t["cart"].status) <= {"active", "abandoned", "recovered", "purchased"}


def test_consent_strictly_increasing(t):
    c = t["consent"]
    assert c.groupby(["customer_id", "channel"]).effective_from.apply(lambda s: s.is_monotonic_increasing and s.is_unique).all()
    assert (c.channel.isin(["email", "sms"])).all() and c.status.isin(["opt_in", "opt_out"]).all()


def test_sessions_and_devices(t):
    s = t["session"]
    assert (pd.to_datetime(s.ended_at) >= pd.to_datetime(s.started_at)).all()
    assert s.groupby("customer_id").device_id.nunique().max() > 1   # multi-device customers exist
