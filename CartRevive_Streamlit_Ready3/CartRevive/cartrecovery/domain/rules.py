"""Pure business rules: no I/O, no randomness, no wall-clock. Every threshold comes from config."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable, Sequence

# ---------- IDs ----------
def cus_id(n: int) -> str: return f"CUS-{n:06d}"
def cart_id(n: int, year: int = 2026) -> str: return f"CART-{year}-{n:06d}"
def cmp_id(n: int, year: int = 2026) -> str: return f"CMP-{year}-{n:06d}"
def send_id(n: int, year: int = 2026) -> str: return f"SEND-{year}-{n:06d}"
def ord_id(n: int, year: int = 2026) -> str: return f"ORD-{year}-{n:06d}"
def att_id(n: int, year: int = 2026) -> str: return f"ATT-{year}-{n:06d}"
def msg_id(cart: str, seq: int = 1) -> str: return f"MSG-{cart}-{seq:02d}"


# ---------- time ----------
def parse(ts: str) -> datetime: return datetime.fromisoformat(ts)
def iso(dt: datetime) -> str: return dt.isoformat(timespec="seconds")


# ---------- cart ----------
def cart_total(line_totals: Iterable[float]) -> float:
    return round(sum(line_totals), 2)

def line_total(unit_price: float, quantity: int) -> float:
    return round(unit_price * quantity, 2)


# ---------- abandonment (time-based) ----------
def abandonment_time(last_activity: datetime, window_minutes: int) -> datetime:
    return last_activity + timedelta(minutes=window_minutes)

def is_abandoned(last_activity: datetime, now: datetime, window_minutes: int,
                 purchased_at: datetime | None = None) -> bool:
    """Abandoned iff inactivity >= window (boundary inclusive) AND no purchase on/before the abandon time."""
    t = abandonment_time(last_activity, window_minutes)
    if now < t:
        return False
    return not (purchased_at is not None and purchased_at <= t)


# ---------- consent ----------
def consent_status_at(events: Sequence[tuple[str, str]], at: datetime) -> str | None:
    """events: (effective_from_iso, status) for ONE customer+channel. Latest event <= at wins; ties by input order."""
    status = None
    for ts, st in sorted(events, key=lambda e: e[0]):
        if parse(ts) <= at:
            status = st
    return status

def has_consent(events: Sequence[tuple[str, str]], at: datetime) -> bool:
    return consent_status_at(events, at) == "opt_in"


# ---------- frequency cap ----------
def within_cap(prior_sends: Iterable[datetime], at: datetime, max_sends: int, per_days: int) -> bool:
    """True if one more send at `at` stays within cap (rolling window, sends at exactly at-per_days no longer count)."""
    lo = at - timedelta(days=per_days)
    return sum(1 for t in prior_sends if lo < t <= at) < max_sends


# ---------- quiet hours (SMS) ----------
def in_quiet_hours(at: datetime, tz_offset_h: int, start: int, end: int) -> bool:
    h = (at + timedelta(hours=tz_offset_h)).hour
    return h >= start or h < end


# ---------- send decision (single place; first failing rule wins, order is documented in RULES.md) ----------
def send_decision(*, purchased: bool, consented: bool, cap_ok: bool, quiet: bool, channel: str) -> str:
    if purchased: return "suppressed_purchased"
    if not consented: return "suppressed_no_consent"
    if not cap_ok: return "suppressed_freq_cap"
    if channel == "sms" and quiet: return "suppressed_quiet_hours"
    return "sent"


# ---------- attribution (deterministic last-click) ----------
def attribute(order_ts: datetime, touches: Sequence[tuple[str, datetime]], window_days: int) -> str | None:
    """touches: (send_id, click_ts). Latest click with 0 <= lag <= window wins; tie -> lexicographically smallest send_id."""
    window = timedelta(days=window_days)
    ok = [(ts, s) for s, ts in touches if timedelta(0) <= order_ts - ts <= window]
    if not ok:
        return None
    best_ts = max(ts for ts, _ in ok)
    return min(s for ts, s in ok if ts == best_ts)
