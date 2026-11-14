"""Data dictionary = single source for DDL, docs and validator enum checks.

Field tuple: (name, sql_type, required, allowed_or_rule, unit, unique)
"""
from __future__ import annotations

ENUMS: dict[str, tuple[str, ...]] = {
    "segment": ("new", "returning", "vip"),
    "channel": ("email", "sms"),
    "consent_status": ("opt_in", "opt_out"),
    "consent_source": ("checkout", "preference_center", "unsubscribe_link", "stop_reply", "admin"),
    "device_type": ("desktop", "mobile", "tablet"),
    "cart_status": ("active", "abandoned", "recovered", "purchased", "expired"),
    "cart_event_type": ("created", "line_added", "qty_updated", "line_removed", "abandoned", "recovered", "purchased", "expired"),
    "variant": ("reminder", "discount_10", "urgency", "free_ship"),
    "campaign_status": ("draft", "running", "completed"),
    "decision": ("sent", "suppressed_no_consent", "suppressed_freq_cap", "suppressed_purchased", "suppressed_quiet_hours"),
    "send_status": ("sent", "delivered", "bounced", "suppressed"),
    "send_event_type": ("delivered", "opened", "clicked", "bounced"),
    "order_status": ("placed", "cancelled", "refunded"),
    "touch_type": ("click",),
    "issue_type": ("message_after_optout", "abandoned_after_purchase",
                   "attribution_double_count", "stale_cart_total"),
}

T, R, O = "TEXT", True, False
# Each entity: pk, fields, fks {col: parent_table.col}, cardinality notes, lifecycle
ENTITIES: dict[str, dict] = {
    "customer": {
        "pk": "customer_id",
        "desc": "Shopper master record. 1:N Consent, Session, Cart, Order.",
        "fields": [
            ("customer_id", T, R, r"CUS-\d{6}", "", True),
            ("email", T, O, "synthetic address @example.com", "", True),
            ("phone", T, O, "synthetic +1555 number", "", False),
            ("full_name", T, R, "synthetic name", "", False),
            ("segment", T, R, "enum segment", "", False),
            ("timezone_offset_h", "INTEGER", R, "-8..+10", "hours from UTC", False),
            ("created_at", T, R, "ISO-8601, <= first session", "UTC", False),
        ],
        "fks": {},
    },
    "consent": {
        "pk": "consent_event_id",
        "desc": "Append-only consent ledger. Current state = latest effective_from <= t per (customer, channel). 1:N from Customer.",
        "fields": [
            ("consent_event_id", T, R, r"CON-\d{7}", "", True),
            ("customer_id", T, R, "FK customer", "", False),
            ("channel", T, R, "enum channel", "", False),
            ("status", T, R, "enum consent_status", "", False),
            ("source", T, R, "enum consent_source", "", False),
            ("effective_from", T, R, "ISO-8601; ordered per (customer,channel)", "UTC", False),
        ],
        "fks": {"customer_id": "customer.customer_id"},
    },
    "session": {
        "pk": "session_id",
        "desc": "Browsing session on one device. 1:N Cart (a cart may span sessions via cart_event.session_id).",
        "fields": [
            ("session_id", T, R, r"SES-\d{7}", "", True),
            ("customer_id", T, R, "FK customer", "", False),
            ("device_id", T, R, "stable per customer device", "", False),
            ("device_type", T, R, "enum device_type", "", False),
            ("started_at", T, R, "ISO-8601", "UTC", False),
            ("ended_at", T, R, ">= started_at", "UTC", False),
        ],
        "fks": {"customer_id": "customer.customer_id"},
    },
    "cart": {
        "pk": "cart_id",
        "desc": "Cart snapshot (current state). History lives in cart_event. Lifecycle: active->abandoned->recovered|expired; active->purchased.",
        "fields": [
            ("cart_id", T, R, r"CART-2026-\d{6}", "", True),
            ("customer_id", T, R, "FK customer", "", False),
            ("session_id", T, R, "FK session (originating)", "", False),
            ("status", T, R, "enum cart_status", "", False),
            ("created_at", T, R, "ISO-8601", "UTC", False),
            ("last_activity_at", T, R, ">= created_at; = max ts of activity events (created/line_added/qty_updated)", "UTC", False),
            ("abandoned_at", T, O, "= last_activity_at + window when abandoned", "UTC", False),
            ("item_count", "INTEGER", R, "= SUM(cart_line.quantity)", "units", False),
            ("total_amount", "REAL", R, "= SUM(cart_line.line_total)", "USD", False),
        ],
        "fks": {"customer_id": "customer.customer_id", "session_id": "session.session_id"},
    },
    "cart_event": {
        "pk": "cart_event_id",
        "desc": "Append-only cart history (adds, qty updates, abandon/recover/purchase). 1:N from Cart.",
        "fields": [
            ("cart_event_id", T, R, r"CEV-\d{7}", "", True),
            ("cart_id", T, R, "FK cart", "", False),
            ("session_id", T, R, "FK session", "", False),
            ("event_type", T, R, "enum cart_event_type", "", False),
            ("event_ts", T, R, "ISO-8601, non-decreasing per cart", "UTC", False),
            ("cart_total_after", "REAL", R, "total at this event", "USD", False),
            ("line_id", T, O, "FK cart_line for line-level mutations", "", False),
            ("quantity_before", "INTEGER", O, "previous line quantity for qty_updated", "units", False),
            ("quantity_after", "INTEGER", O, "new line quantity for line_added/qty_updated", "units", False),
        ],
        "fks": {"cart_id": "cart.cart_id", "session_id": "session.session_id", "line_id": "cart_line.line_id"},
    },
    "cart_line": {
        "pk": "line_id",
        "desc": "Items in a cart. 1:N from Cart.",
        "fields": [
            ("line_id", T, R, r"LINE-\d{7}", "", True),
            ("cart_id", T, R, "FK cart", "", False),
            ("sku", T, R, r"SKU-\d{4}", "", False),
            ("product_name", T, R, "", "", False),
            ("category", T, R, "", "", False),
            ("unit_price", "REAL", R, "> 0", "USD", False),
            ("quantity", "INTEGER", R, ">= 1", "units", False),
            ("line_total", "REAL", R, "= ROUND(unit_price*quantity,2)", "USD", False),
            ("added_at", T, R, "ISO-8601", "UTC", False),
        ],
        "fks": {"cart_id": "cart.cart_id"},
    },
    "message_template": {
        "pk": "template_id",
        "desc": "Reusable copy per variant+channel. 1:N Campaign targeting not required.",
        "fields": [
            ("template_id", T, R, r"TPL-\d{3}", "", True),
            ("variant", T, R, "enum variant", "", False),
            ("channel", T, R, "enum channel", "", False),
            ("subject", T, O, "email only", "", False),
            ("body", T, R, "placeholders {first_name},{cart_total}", "", False),
            ("discount_pct", "INTEGER", R, "0..30", "percent", False),
        ],
        "fks": {},
    },
    "campaign": {
        "pk": "campaign_id",
        "desc": "A scheduled recovery campaign for one variant+channel over a date range. N:M Cart via campaign_target.",
        "fields": [
            ("campaign_id", T, R, r"CMP-2026-\d{6}", "", True),
            ("name", T, R, "", "", False),
            ("variant", T, R, "enum variant", "", False),
            ("channel", T, R, "enum channel", "", False),
            ("template_id", T, R, "FK message_template; variant+channel must match", "", False),
            ("starts_at", T, R, "ISO-8601", "UTC", False),
            ("ends_at", T, R, "> starts_at", "UTC", False),
            ("status", T, R, "enum campaign_status", "", False),
        ],
        "fks": {"template_id": "message_template.template_id"},
    },
    "campaign_target": {
        "pk": "campaign_id,cart_id",
        "desc": "Bridge: which abandoned carts a campaign evaluated (N:M Campaign-Cart).",
        "fields": [
            ("campaign_id", T, R, "FK campaign", "", False),
            ("cart_id", T, R, "FK cart", "", False),
            ("evaluated_at", T, R, "ISO-8601", "UTC", False),
        ],
        "fks": {"campaign_id": "campaign.campaign_id", "cart_id": "cart.cart_id"},
    },
    "send_attempt": {
        "pk": "send_id",
        "desc": "Every dispatch decision, including suppressed ones (kept for traceability). 1:N from Campaign and Cart.",
        "fields": [
            ("send_id", T, R, r"SEND-2026-\d{6}", "", True),
            ("message_id", T, R, r"MSG-<cart_id>-\d{2}", "", True),
            ("campaign_id", T, R, "FK campaign", "", False),
            ("cart_id", T, R, "FK cart", "", False),
            ("customer_id", T, R, "FK customer", "", False),
            ("channel", T, R, "enum channel", "", False),
            ("decision", T, R, "enum decision", "", False),
            ("decision_ts", T, R, "ISO-8601; cart abandoned_at <= decision_ts", "UTC", False),
            ("sent_at", T, O, "required iff decision=sent; >= decision_ts", "UTC", False),
            ("status", T, R, "enum send_status", "", False),
        ],
        "fks": {"campaign_id": "campaign.campaign_id", "cart_id": "cart.cart_id",
                "customer_id": "customer.customer_id"},
    },
    "send_event": {
        "pk": "event_id",
        "desc": "Delivery/open/click events per send. 1:N from SendAttempt.",
        "fields": [
            ("event_id", T, R, r"EVT-\d{7}", "", True),
            ("send_id", T, R, "FK send_attempt (decision=sent)", "", False),
            ("event_type", T, R, "enum send_event_type", "", False),
            ("event_ts", T, R, "ISO-8601, >= sent_at", "UTC", False),
        ],
        "fks": {"send_id": "send_attempt.send_id"},
    },
    "order": {
        "pk": "order_id",
        "desc": "Placed purchase. Optional link to the originating Cart (guest/other-cart orders have NULL).",
        "fields": [
            ("order_id", T, R, r"ORD-2026-\d{6}", "", True),
            ("customer_id", T, R, "FK customer", "", False),
            ("cart_id", T, O, "FK cart", "", False),
            ("ordered_at", T, R, "ISO-8601", "UTC", False),
            ("total_amount", "REAL", R, "= cart.total_amount at purchase (>=0)", "USD", False),
            ("status", T, R, "enum order_status", "", False),
        ],
        "fks": {"customer_id": "customer.customer_id", "cart_id": "cart.cart_id"},
    },
    "attribution": {
        "pk": "attribution_id",
        "desc": "Bridge: order credited to a send/campaign (N:M Order-Send). Deterministic last-click within window; credit sums to 1 per order.",
        "fields": [
            ("attribution_id", T, R, r"ATT-2026-\d{6}", "", True),
            ("order_id", T, R, "FK order", "", False),
            ("send_id", T, R, "FK send_attempt", "", False),
            ("campaign_id", T, R, "FK campaign", "", False),
            ("touch_type", T, R, "enum touch_type", "", False),
            ("touch_ts", T, R, "ISO-8601; touch_ts <= ordered_at", "UTC", False),
            ("lag_minutes", "INTEGER", R, "= ordered_at - touch_ts; <= window", "minutes", False),
            ("credit", "REAL", R, "0 < credit <= 1", "fraction", False),
        ],
        "fks": {"order_id": "order.order_id", "send_id": "send_attempt.send_id",
                "campaign_id": "campaign.campaign_id"},
    },
}

# Ensure referenced cart_line rows are inserted before cart_event rows in SQL/SQLite exports.
ENTITIES = {name: value for name, value in ENTITIES.items() if name != "cart_event"}
_cart_line_index = list(ENTITIES).index("cart_line") + 1
ENTITIES = {
    **{name: value for i, (name, value) in enumerate(ENTITIES.items()) if i < _cart_line_index},
    "cart_event": {
        "pk": "cart_event_id",
        "desc": "Append-only cart history (adds, qty updates, abandon/recover/purchase). 1:N from Cart.",
        "fields": [
            ("cart_event_id", T, R, r"CEV-\d{7}", "", True),
            ("cart_id", T, R, "FK cart", "", False),
            ("session_id", T, R, "FK session", "", False),
            ("event_type", T, R, "enum cart_event_type", "", False),
            ("event_ts", T, R, "ISO-8601, non-decreasing per cart", "UTC", False),
            ("cart_total_after", "REAL", R, "total at this event", "USD", False),
            ("line_id", T, O, "FK cart_line for line-level mutations", "", False),
            ("quantity_before", "INTEGER", O, "previous line quantity for qty_updated", "units", False),
            ("quantity_after", "INTEGER", O, "new line quantity for line_added/qty_updated", "units", False),
        ],
        "fks": {"cart_id": "cart.cart_id", "session_id": "session.session_id", "line_id": "cart_line.line_id"},
    },
    **{name: value for i, (name, value) in enumerate(ENTITIES.items()) if i >= _cart_line_index},
}

# Ground truth lives apart: never imported by generator business logic or features.
GROUND_TRUTH_FIELDS = ("gt_id", "issue_type", "entity", "entity_id", "expected_decision", "note")
