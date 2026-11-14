from datetime import datetime, timedelta

from cartrecovery.domain import rules as r
from cartrecovery.repositories.schema import create_db

T0 = datetime(2026, 3, 1, 12, 0)


def test_ids():
    assert r.cus_id(1) == "CUS-000001" and r.cart_id(1) == "CART-2026-000001"
    assert r.msg_id("CART-2026-000001") == "MSG-CART-2026-000001-01"


def test_abandonment_boundary():
    assert not r.is_abandoned(T0, T0 + timedelta(minutes=59), 60)
    assert r.is_abandoned(T0, T0 + timedelta(minutes=60), 60)          # exactly at window
    assert not r.is_abandoned(T0, T0 + timedelta(hours=5), 60, purchased_at=T0 + timedelta(minutes=30))


def test_consent_latest_wins():
    ev = [("2026-01-01T00:00:00", "opt_in"), ("2026-02-01T00:00:00", "opt_out")]
    assert r.has_consent(ev, datetime(2026, 1, 15))
    assert not r.has_consent(ev, datetime(2026, 2, 1))                 # effective at exact timestamp
    assert not r.has_consent([], T0)


def test_frequency_cap_boundary():
    prior = [T0 - timedelta(days=7), T0 - timedelta(days=2)]
    assert r.within_cap(prior, T0, 2, 7)                               # 7-day-old send has left the window
    assert not r.within_cap(prior + [T0 - timedelta(days=1)], T0, 2, 7)


def test_decision_precedence():
    kw = dict(purchased=False, consented=True, cap_ok=True, quiet=False, channel="email")
    assert r.send_decision(**kw) == "sent"
    assert r.send_decision(**{**kw, "consented": False, "cap_ok": False}) == "suppressed_no_consent"
    assert r.send_decision(**{**kw, "purchased": True}) == "suppressed_purchased"


def test_attribution_deterministic():
    o = T0
    touches = [("SEND-B", o - timedelta(days=1)), ("SEND-A", o - timedelta(days=1)), ("SEND-C", o - timedelta(days=8))]
    assert r.attribute(o, touches, 7) == "SEND-A"                      # tie -> smallest id; 8d touch ignored
    assert r.attribute(o, [("S", o - timedelta(days=7))], 7) == "S"    # window edge inclusive
    assert r.attribute(o, [("S", o + timedelta(minutes=1))], 7) is None


def test_schema_builds():
    con = create_db()
    n = con.execute("select count(*) from sqlite_master where type='table'").fetchone()[0]
    assert n == 13
