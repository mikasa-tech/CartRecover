# CartRevive — Synthetic E-Commerce Cart Recovery Pipeline

CartRevive generates a reproducible synthetic e-commerce dataset, simulates abandoned-cart campaigns, injects four labelled exception scenarios, validates business rules, calculates campaign metrics, and exports the result in multiple formats.

## Requirements

- Python 3.10+
- Dependencies in `requirements.txt`

## Setup (Windows PowerShell)

Open PowerShell in the extracted `CartRevive` project directory, then run:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

If PowerShell blocks activation, use the venv interpreter directly:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Run the pipeline

Starter-scale run (5% of configured full volume):

```powershell
python -m cartrecovery.services.pipeline --scale 0.05 --out output\starter --validate
```

The default export formats are CSV, JSON, SQL seed and SQLite. Choose formats explicitly if needed:

```powershell
python -m cartrecovery.services.pipeline --scale 0.05 --formats csv,json,sql,sqlite --out output\starter
```

Run the fixed-seed two-run reproducibility proof:

```powershell
python -m cartrecovery.services.pipeline --scale 0.05 --out output\starter --validate --repro-check
```

Use `--seed 12345` to override the seed. The default seed is `20261022` from `config.yaml`. `--scale` must be greater than zero and at most one.

## Output layout

This project bundle includes both `output/starter` (5% sample with a two-run reproducibility proof) and `output/full_scale` (50,000-cart full dataset). The `.gitignore` intentionally excludes generated output from ordinary Git commits because the full-scale SQL seed is larger than GitHub's per-file upload limit; include the generated output folders when distributing the ZIP submission.

- `data/*.csv` — the 13 relational input tables. The configured `campaigns: 24` creates 24 campaign-wave records; `scale.sends: 10000` is a minimum target for clean dispatched SendAttempts (decision=`sent`), not a campaign count.
- `json/*.json` — one JSON array per relational table.
- `seed.sql` — schema and deterministic INSERT statements. Ground truth is deliberately excluded.
- `cartrevive.db` — SQLite database containing only the application tables.
- `ground_truth.csv` and `ground_truth.json` — separate labels for the four injected anomalies; these must not be used as model or business-rule input features.
- `metrics/per_send.csv` — one row per dispatched send, including zero-conversion sends.
- `metrics/by_variant.csv`, `metrics/by_channel.csv`, `metrics/by_month.csv` — campaign performance rollups.
- `metrics/overall.json` — overall metric control totals.
- `validation_summary.json` — structured rule execution counts and detected violations.
- `reproducibility.json` — checksum comparison from two independent runs when `--repro-check` is used.
- `manifest.json` — SHA-256 hashes, file sizes and row counts for generated artifacts, plus hashes of source files. The manifest does not checksum itself.

Text exports use explicit LF (`\n`) line endings so checksums do not vary between Windows and Linux.

## Metric definitions

Metrics are calculated from the clean baseline **before** deliberate anomaly injection. This prevents known corrupted rows from inflating or depressing campaign performance.

- **Dispatched send:** `send_attempt.decision == "sent"`.
- **Converted order:** an attributed order with `order.status == "placed"`. Cancelled and refunded orders are excluded from realized revenue.
- **Recovered cart:** an attributed placed order whose cart is in `recovered` state and whose `order.cart_id` matches the cart targeted by the credited send.
- **Recovery rate:** distinct recovered carts divided by distinct carts contacted in the corresponding segment.
- **Revenue recovered:** credited revenue from same-cart recoveries.
- **Attributed revenue:** credited revenue from all placed orders attributed to a send, including orders from another cart belonging to the same customer.
- **Revenue attribution per send:** attributed revenue divided by dispatched send count.
- **Month:** UTC calendar month derived from `send_attempt.sent_at`.

Variant, channel and month summaries are independent partitions. Their additive counts and revenue must reconcile to the overall totals; rates and per-send averages are not additive.

## Validation and expected anomaly behavior

The pipeline runs all nine validator groups and writes a per-rule execution summary, including zero-violation checks. The summary's overall `status` is `PASS` when all detected violations reconcile exactly to the separate ground-truth labels; `rule_status` remains `FAIL` when the intentionally injected anomalies are present. Cart line mutation events include `line_id`, `quantity_before`, and `quantity_after` so quantity changes can be reconstructed; non-line lifecycle events leave these fields NULL. The configured `late_purchase_after_days` controls the late click-to-purchase response band. The pipeline fails before export if clean dispatched sends fall below `scale.sends * --scale`.

The four deliberately injected scenarios remain labelled separately:

1. Message sent after opt-out.
2. Abandonment event after an earlier purchase.
3. One order credited to multiple campaigns.
4. Cart snapshot total stale after a quantity update.

A clean baseline should have no violations. The anomaly-injected dataset should detect every ground-truth label with no false positives or unexplained violations.

## Tests

```powershell
python -m pytest -q
```

The tests cover generation, campaign and validation behavior, minimum dispatched-send enforcement, line-level cart mutation history, real click-backed attribution exceptions, metrics reconciliation, export formats, manifest hashes, SQLite/SQL integrity and two-run checksum reproducibility.

## Interactive Streamlit dashboard

CartRevive includes an interactive operations dashboard for exploring the generated snapshot. It provides:

- Executive KPIs and recovery-revenue trends.
- Campaign and channel filtering with wave-level send analysis.
- Cart lookup with line items, lifecycle timeline, contact history and orders.
- Exception review with ground-truth evidence lookup.
- Data-quality controls, validation groups, table inventory and artifact checksums.

Launch it from the project root:

```powershell
pip install -r requirements.txt
streamlit run app.py
```

The dashboard defaults to `output/full_scale` and lets you switch to `output/starter` in the sidebar. It reads the existing generated files; it does not send messages or modify the source dataset. Campaign KPI rollups are computed from the clean baseline before injected anomalies, while the exception centre exposes the deliberately corrupted records separately.
