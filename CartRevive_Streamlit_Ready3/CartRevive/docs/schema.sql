CREATE TABLE "customer" (
  "customer_id" TEXT NOT NULL PRIMARY KEY,
  "email" TEXT UNIQUE,
  "phone" TEXT,
  "full_name" TEXT NOT NULL,
  "segment" TEXT NOT NULL CHECK ("segment" IN ('new','returning','vip')),
  "timezone_offset_h" INTEGER NOT NULL,
  "created_at" TEXT NOT NULL
);
CREATE TABLE "consent" (
  "consent_event_id" TEXT NOT NULL PRIMARY KEY,
  "customer_id" TEXT NOT NULL,
  "channel" TEXT NOT NULL CHECK ("channel" IN ('email','sms')),
  "status" TEXT NOT NULL CHECK ("status" IN ('opt_in','opt_out')),
  "source" TEXT NOT NULL CHECK ("source" IN ('checkout','preference_center','unsubscribe_link','stop_reply','admin')),
  "effective_from" TEXT NOT NULL,
  FOREIGN KEY ("customer_id") REFERENCES "customer"("customer_id")
);
CREATE INDEX idx_consent_fk ON "consent"(customer_id);
CREATE TABLE "session" (
  "session_id" TEXT NOT NULL PRIMARY KEY,
  "customer_id" TEXT NOT NULL,
  "device_id" TEXT NOT NULL,
  "device_type" TEXT NOT NULL CHECK ("device_type" IN ('desktop','mobile','tablet')),
  "started_at" TEXT NOT NULL,
  "ended_at" TEXT NOT NULL,
  FOREIGN KEY ("customer_id") REFERENCES "customer"("customer_id")
);
CREATE INDEX idx_session_fk ON "session"(customer_id);
CREATE TABLE "cart" (
  "cart_id" TEXT NOT NULL PRIMARY KEY,
  "customer_id" TEXT NOT NULL,
  "session_id" TEXT NOT NULL,
  "status" TEXT NOT NULL CHECK ("status" IN ('active','abandoned','recovered','purchased','expired')),
  "created_at" TEXT NOT NULL,
  "last_activity_at" TEXT NOT NULL,
  "abandoned_at" TEXT,
  "item_count" INTEGER NOT NULL,
  "total_amount" REAL NOT NULL,
  FOREIGN KEY ("customer_id") REFERENCES "customer"("customer_id"),
  FOREIGN KEY ("session_id") REFERENCES "session"("session_id")
);
CREATE INDEX idx_cart_fk ON "cart"(customer_id, session_id);
CREATE TABLE "cart_line" (
  "line_id" TEXT NOT NULL PRIMARY KEY,
  "cart_id" TEXT NOT NULL,
  "sku" TEXT NOT NULL,
  "product_name" TEXT NOT NULL,
  "category" TEXT NOT NULL,
  "unit_price" REAL NOT NULL,
  "quantity" INTEGER NOT NULL,
  "line_total" REAL NOT NULL,
  "added_at" TEXT NOT NULL,
  FOREIGN KEY ("cart_id") REFERENCES "cart"("cart_id")
);
CREATE INDEX idx_cart_line_fk ON "cart_line"(cart_id);
CREATE TABLE "cart_event" (
  "cart_event_id" TEXT NOT NULL PRIMARY KEY,
  "cart_id" TEXT NOT NULL,
  "session_id" TEXT NOT NULL,
  "event_type" TEXT NOT NULL CHECK ("event_type" IN ('created','line_added','qty_updated','line_removed','abandoned','recovered','purchased','expired')),
  "event_ts" TEXT NOT NULL,
  "cart_total_after" REAL NOT NULL,
  "line_id" TEXT,
  "quantity_before" INTEGER,
  "quantity_after" INTEGER,
  FOREIGN KEY ("cart_id") REFERENCES "cart"("cart_id"),
  FOREIGN KEY ("session_id") REFERENCES "session"("session_id"),
  FOREIGN KEY ("line_id") REFERENCES "cart_line"("line_id")
);
CREATE INDEX idx_cart_event_fk ON "cart_event"(cart_id, session_id, line_id);
CREATE TABLE "message_template" (
  "template_id" TEXT NOT NULL PRIMARY KEY,
  "variant" TEXT NOT NULL CHECK ("variant" IN ('reminder','discount_10','urgency','free_ship')),
  "channel" TEXT NOT NULL CHECK ("channel" IN ('email','sms')),
  "subject" TEXT,
  "body" TEXT NOT NULL,
  "discount_pct" INTEGER NOT NULL
);
CREATE TABLE "campaign" (
  "campaign_id" TEXT NOT NULL PRIMARY KEY,
  "name" TEXT NOT NULL,
  "variant" TEXT NOT NULL CHECK ("variant" IN ('reminder','discount_10','urgency','free_ship')),
  "channel" TEXT NOT NULL CHECK ("channel" IN ('email','sms')),
  "template_id" TEXT NOT NULL,
  "starts_at" TEXT NOT NULL,
  "ends_at" TEXT NOT NULL,
  "status" TEXT NOT NULL CHECK ("status" IN ('draft','running','completed')),
  FOREIGN KEY ("template_id") REFERENCES "message_template"("template_id")
);
CREATE INDEX idx_campaign_fk ON "campaign"(template_id);
CREATE TABLE "campaign_target" (
  "campaign_id" TEXT NOT NULL,
  "cart_id" TEXT NOT NULL,
  "evaluated_at" TEXT NOT NULL,
  PRIMARY KEY (campaign_id,cart_id),
  FOREIGN KEY ("campaign_id") REFERENCES "campaign"("campaign_id"),
  FOREIGN KEY ("cart_id") REFERENCES "cart"("cart_id")
);
CREATE INDEX idx_campaign_target_fk ON "campaign_target"(campaign_id, cart_id);
CREATE TABLE "send_attempt" (
  "send_id" TEXT NOT NULL PRIMARY KEY,
  "message_id" TEXT NOT NULL UNIQUE,
  "campaign_id" TEXT NOT NULL,
  "cart_id" TEXT NOT NULL,
  "customer_id" TEXT NOT NULL,
  "channel" TEXT NOT NULL CHECK ("channel" IN ('email','sms')),
  "decision" TEXT NOT NULL CHECK ("decision" IN ('sent','suppressed_no_consent','suppressed_freq_cap','suppressed_purchased','suppressed_quiet_hours')),
  "decision_ts" TEXT NOT NULL,
  "sent_at" TEXT,
  "status" TEXT NOT NULL CHECK ("status" IN ('sent','delivered','bounced','suppressed')),
  FOREIGN KEY ("campaign_id") REFERENCES "campaign"("campaign_id"),
  FOREIGN KEY ("cart_id") REFERENCES "cart"("cart_id"),
  FOREIGN KEY ("customer_id") REFERENCES "customer"("customer_id")
);
CREATE INDEX idx_send_attempt_fk ON "send_attempt"(campaign_id, cart_id, customer_id);
CREATE TABLE "send_event" (
  "event_id" TEXT NOT NULL PRIMARY KEY,
  "send_id" TEXT NOT NULL,
  "event_type" TEXT NOT NULL CHECK ("event_type" IN ('delivered','opened','clicked','bounced')),
  "event_ts" TEXT NOT NULL,
  FOREIGN KEY ("send_id") REFERENCES "send_attempt"("send_id")
);
CREATE INDEX idx_send_event_fk ON "send_event"(send_id);
CREATE TABLE "order" (
  "order_id" TEXT NOT NULL PRIMARY KEY,
  "customer_id" TEXT NOT NULL,
  "cart_id" TEXT,
  "ordered_at" TEXT NOT NULL,
  "total_amount" REAL NOT NULL,
  "status" TEXT NOT NULL CHECK ("status" IN ('placed','cancelled','refunded')),
  FOREIGN KEY ("customer_id") REFERENCES "customer"("customer_id"),
  FOREIGN KEY ("cart_id") REFERENCES "cart"("cart_id")
);
CREATE INDEX idx_order_fk ON "order"(customer_id, cart_id);
CREATE TABLE "attribution" (
  "attribution_id" TEXT NOT NULL PRIMARY KEY,
  "order_id" TEXT NOT NULL,
  "send_id" TEXT NOT NULL,
  "campaign_id" TEXT NOT NULL,
  "touch_type" TEXT NOT NULL CHECK ("touch_type" IN ('click')),
  "touch_ts" TEXT NOT NULL,
  "lag_minutes" INTEGER NOT NULL,
  "credit" REAL NOT NULL,
  FOREIGN KEY ("order_id") REFERENCES "order"("order_id"),
  FOREIGN KEY ("send_id") REFERENCES "send_attempt"("send_id"),
  FOREIGN KEY ("campaign_id") REFERENCES "campaign"("campaign_id")
);
CREATE INDEX idx_attribution_fk ON "attribution"(order_id, send_id, campaign_id);

-- separate ground-truth database
CREATE TABLE "ground_truth" (
  "gt_id" TEXT,
  "issue_type" TEXT,
  "entity" TEXT,
  "entity_id" TEXT,
  "expected_decision" TEXT,
  "note" TEXT,
  PRIMARY KEY ("gt_id")
);
