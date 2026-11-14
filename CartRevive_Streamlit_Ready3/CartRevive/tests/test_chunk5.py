from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pandas as pd

from cartrecovery.config import load_config
from cartrecovery.services.exporter import export_datasets
from cartrecovery.services.metrics import compute_metrics, validate_metrics
from cartrecovery.services.pipeline import build_dataset, build_with_anomalies, validate_send_volume
from cartrecovery.services.validator import validate_detailed
from cartrecovery.services.reproducibility import run_reproducibility

CFG = load_config()


def test_metrics_reconcile_across_variant_channel_and_month():
    tables = build_dataset(CFG, 0.05)
    metrics = compute_metrics(tables)
    validate_metrics(metrics)

    sent = tables["send_attempt"].query("decision == 'sent'")
    assert len(metrics["per_send"]) == len(sent)
    assert metrics["per_send"].send_id.is_unique
    overall = metrics["overall"].iloc[0]
    for key in ("by_variant", "by_channel", "by_month"):
        grouped = metrics[key]
        assert grouped.sends.sum() == overall.sends
        assert grouped.attributed_orders.sum() == overall.attributed_orders
        assert grouped.recovered_carts.sum() == overall.recovered_carts
        assert abs(grouped.attributed_revenue.sum() - overall.attributed_revenue) < 0.01
        assert abs(grouped.revenue_recovered.sum() - overall.revenue_recovered) < 0.01
        assert grouped.recovery_rate.between(0, 1).all()
    assert (metrics["per_send"].revenue_attributed >= 0).all()


def test_all_export_formats_and_manifest(tmp_path):
    tables = build_dataset(CFG, 0.05)
    metrics = compute_metrics(tables)
    manifest = export_datasets(
        tables, tmp_path, formats=("csv", "json", "sql", "sqlite"),
        ground_truth=pd.DataFrame([{
            "gt_id": "GT-0001", "issue_type": "sample", "entity": "cart",
            "entity_id": "CART-2026-000001", "expected_decision": "review", "note": "test only",
        }]),
        metrics=metrics, seed=CFG["seed"], scale=0.05,
    )

    assert (tmp_path / "data" / "cart.csv").exists()
    assert (tmp_path / "json" / "cart.json").exists()
    assert (tmp_path / "seed.sql").exists()
    assert (tmp_path / "cartrevive.db").exists()
    assert (tmp_path / "ground_truth.csv").exists()
    assert (tmp_path / "manifest.json").exists()

    csv_bytes = (tmp_path / "data" / "cart.csv").read_bytes()
    json_bytes = (tmp_path / "json" / "cart.json").read_bytes()
    sql_bytes = (tmp_path / "seed.sql").read_bytes()
    assert b"\r\n" not in csv_bytes + json_bytes + sql_bytes
    assert len(json.loads(json_bytes)) == len(tables["cart"])

    # The SQL seed is independently executable, and preserves table row counts.
    con = sqlite3.connect(":memory:")
    con.executescript(sql_bytes.decode("utf-8"))
    assert con.execute("select count(*) from cart").fetchone()[0] == len(tables["cart"])
    con.close()

    con = sqlite3.connect(tmp_path / "cartrevive.db")
    assert con.execute("select count(*) from cart").fetchone()[0] == len(tables["cart"])
    assert con.execute("pragma foreign_key_check").fetchall() == []
    con.close()

    listed = {x["path"]: x for x in manifest["artifacts"]}
    cart_record = listed["data/cart.csv"]
    assert cart_record["row_count"] == len(tables["cart"])
    assert cart_record["sha256"] == hashlib.sha256((tmp_path / "data" / "cart.csv").read_bytes()).hexdigest()
    loaded = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert loaded["artifact_count"] == len(manifest["artifacts"])


def test_two_run_reproducibility_all_formats():
    proof = run_reproducibility(CFG, 0.05, CFG["seed"])
    assert proof["reproducible"], proof["mismatches"]
    assert proof["matched_files"] == proof["file_count"]
    assert proof["file_count"] > 10


def test_validator_structured_summary_counts_every_rule():
    tables, truth = build_with_anomalies(CFG, 0.05)
    violations, summary = validate_detailed(tables, CFG)
    assert summary["check_groups_executed"] == 9
    assert summary["rule_checks_executed"] == len(summary["checks"])
    assert all(check["runs"] == 1 for check in summary["checks"])
    assert summary["total_violations"] == len(violations)
    assert summary["status"] == "FAIL"  # injected anomalies are expected to be detected
    assert set(violations.issue_type.dropna()) == set(truth.issue_type)


def test_minimum_dispatched_send_target_is_enforced():
    cfg = {"scale": {"sends": 10000}}
    tables = {"send_attempt": pd.DataFrame({"decision": ["sent"] * 10 + ["suppressed"] * 3})}
    assert validate_send_volume(tables, cfg, 0.001) == (10, 10)
    tables["send_attempt"] = pd.DataFrame({"decision": ["sent"] * 9 + ["suppressed"] * 4})
    import pytest
    with pytest.raises(ValueError, match="Dispatched-send target not met"):
        validate_send_volume(tables, cfg, 0.001)


def test_cart_line_mutation_events_capture_line_and_quantity_delta():
    tables = build_dataset(CFG, 0.05)
    events = tables["cart_event"]
    mutations = events[events.event_type.isin(["line_added", "qty_updated"])]
    assert mutations.line_id.notna().all()
    assert mutations.quantity_after.notna().all()
    updates = mutations[mutations.event_type.eq("qty_updated")]
    assert updates.quantity_before.notna().all()
    assert (updates.quantity_before != updates.quantity_after).all()
