import re
from pathlib import Path

import pytest

from cartrecovery.config import load_config
from cartrecovery.services.pipeline import build_dataset, build_with_anomalies
from cartrecovery.services.validator import compare, validate

CFG = load_config()
TYPES = set(CFG["anomalies"])


@pytest.fixture(scope="module")
def clean():
    return build_dataset(CFG, 0.1)


@pytest.fixture(scope="module")
def dirty():
    return build_with_anomalies(CFG, 0.1)


def test_clean_baseline_has_zero_violations(clean):
    assert validate(clean, CFG).empty


def test_validator_detects_exactly_the_labelled_anomalies(dirty):
    t, truth = dirty
    c = compare(validate(t, CFG), truth)
    assert c["passed"], (c["missed"], c["false_positive"], c["unexplained"].head())
    assert set(truth.issue_type) == TYPES                        # all 4 required edge cases present
    assert truth.groupby("issue_type").size().min() >= 1


def test_ground_truth_shape(dirty):
    _, truth = dirty
    assert list(truth.columns) == ["gt_id", "issue_type", "entity", "entity_id", "expected_decision", "note"]
    assert truth.gt_id.is_unique and truth.gt_id.str.match(r"^GT-\d{4}$").all()


def test_ground_truth_never_leaks_into_logic():
    for f in ("generator", "campaigns", "validator", "pipeline"):
        src = (Path(__file__).parents[1] / "cartrecovery/services" / f"{f}.py").read_text()
        head = src.split("def main")[0]
        assert not re.search(r"import .*(anomalies|ground)", head.replace("from cartrecovery.services.anomalies import inject", ""))
    t, _ = build_with_anomalies(CFG, 0.05)
    assert "issue_type" not in {c for df in t.values() for c in df.columns}   # labels not in any input table


def test_unlabelled_corruption_is_flagged_unexplained(clean):
    t = {k: v.copy() for k, v in clean.items()}
    t["send_attempt"].loc[t["send_attempt"].index[0], "cart_id"] = "CART-2026-999999"
    c = compare(validate(t, CFG), build_with_anomalies(CFG, 0.05)[1].iloc[0:0])
    assert not c["passed"] and (c["unexplained"].check == "fk_integrity").any()


def test_double_attribution_uses_real_click_events(dirty):
    tables, truth = dirty
    duplicate_orders = tables["attribution"].groupby("order_id").filter(lambda g: len(g) > 1)
    clicks = tables["send_event"].query("event_type == 'clicked'")[["send_id", "event_ts"]]
    joined = duplicate_orders.merge(clicks, on="send_id", how="left")
    assert joined.event_ts.notna().all()
    assert (joined.touch_ts == joined.event_ts).all()


def test_cart_event_details_are_validated(clean):
    from cartrecovery.services.validator import check_cart_event_details
    assert all(frame.empty for frame in check_cart_event_details(clean, CFG))
    broken = {k: v.copy() for k, v in clean.items()}
    idx = broken["cart_event"].index[broken["cart_event"].event_type.eq("qty_updated")][0]
    broken["cart_event"].loc[idx, "quantity_after"] = broken["cart_event"].loc[idx, "quantity_before"]
    issues = check_cart_event_details(broken, CFG)
    assert any((not f.empty) and f.check.iloc[0] == "qty_updated_quantity_delta" for f in issues)
