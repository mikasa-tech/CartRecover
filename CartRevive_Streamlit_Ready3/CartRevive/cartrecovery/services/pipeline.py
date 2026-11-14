"""End-to-end CartRevive data, anomaly, metrics, export and validation pipeline."""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from cartrecovery.config import load_config
from cartrecovery.services.campaigns import run_campaigns
from cartrecovery.services.generator import generate_masters


def build_dataset(cfg: dict, scale: float = 1.0, seed: int | None = None) -> dict[str, pd.DataFrame]:
    """Build a clean baseline; no anomaly labels or injection enter business logic."""
    t = generate_masters(cfg, scale, seed)
    t.update(run_campaigns(cfg, t))
    return t


def validate_send_volume(tables: dict[str, pd.DataFrame], cfg: dict, scale: float) -> tuple[int, int]:
    """Enforce the configured minimum number of clean, dispatched sends."""
    target = int(round(cfg["scale"]["sends"] * scale))
    actual = int(tables["send_attempt"]["decision"].eq("sent").sum())
    if actual < target:
        raise ValueError(
            f"Dispatched-send target not met: expected at least {target:,} "
            f"at scale {scale:g}, generated {actual:,}. Adjust campaign targeting "
            "or dataset scale before exporting."
        )
    return actual, target


def build_with_anomalies(cfg: dict, scale: float = 1.0, seed: int | None = None):
    """Clean baseline first, then labelled edge cases. Returns (tables, truth)."""
    from cartrecovery.services.anomalies import inject
    return inject(cfg, build_dataset(cfg, scale, seed), scale)


def main() -> None:
    ap = argparse.ArgumentParser(description="CartRevive end-to-end pipeline")
    ap.add_argument("--scale", type=float, default=0.02)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--out", default="output/starter")
    ap.add_argument("--formats", default="csv,json,sql,sqlite",
                    help="comma-separated export formats: csv,json,sql,sqlite")
    ap.add_argument("--validate", action="store_true",
                    help="return a non-zero exit code if validation detects unexplained issues")
    ap.add_argument("--repro-check", action="store_true",
                    help="build twice with the same seed and compare every artifact checksum")
    a = ap.parse_args()

    from cartrecovery.services.anomalies import inject
    from cartrecovery.services.exporter import export_datasets, write_json_file, build_manifest
    from cartrecovery.services.metrics import compute_metrics, validate_metrics
    from cartrecovery.services.validator import compare, validate_detailed

    cfg = load_config()
    seed = cfg["seed"] if a.seed is None else a.seed
    formats = [x.strip().lower() for x in a.formats.split(",") if x.strip()]
    if not 0 < a.scale <= 1:
        ap.error("--scale must be greater than 0 and at most 1")

    # Metrics are calculated on the clean baseline before deliberate anomaly injection.
    clean = build_dataset(cfg, a.scale, seed)
    dispatched, send_target = validate_send_volume(clean, cfg, a.scale)
    metrics = compute_metrics(clean)
    validate_metrics(metrics)
    tables, truth = inject(cfg, clean, a.scale)

    out = Path(a.out)
    manifest = export_datasets(
        tables, out, formats=formats, ground_truth=truth, metrics=metrics,
        seed=seed, scale=a.scale, source_root=Path(__file__).resolve().parents[2],
    )

    violations, validation_summary = validate_detailed(tables, cfg)
    comparison = compare(violations, truth)
    # Raw rule violations are expected because the dataset deliberately contains
    # labelled anomalies. Overall validation passes only when every violation is
    # explained by ground truth and there are no missed/false-positive labels.
    validation_summary["rule_status"] = validation_summary["status"]
    validation_summary["status"] = "PASS" if comparison["passed"] else "FAIL"
    validation_summary["expected_violation_count"] = int(len(violations))
    validation_summary["label_comparison"] = {
        "detected": comparison["detected"],
        "labelled": comparison["labelled"],
        "missed_count": len(comparison["missed"]),
        "false_positive_count": len(comparison["false_positive"]),
        "unexplained_count": len(comparison["unexplained"]),
        "passed": comparison["passed"],
        "missed": [list(x) for x in comparison["missed"]],
        "false_positive": [list(x) for x in comparison["false_positive"]],
        "unexplained": comparison["unexplained"].to_dict(orient="records"),
    }
    write_json_file(out / "validation_summary.json", validation_summary)
    # Rebuild the manifest after the validation summary is present.
    manifest = build_manifest(out, seed, a.scale, Path(__file__).resolve().parents[2])

    sends = tables["send_attempt"]
    print(f"CartRevive complete | seed={seed} | scale={a.scale}")
    print(f"tables={len(tables)} | carts={len(tables['cart']):,} | sends={len(sends):,}")
    print(f"clean dispatched sends: {dispatched:,} / minimum target {send_target:,}")
    print(f"decisions: {sends.decision.value_counts().to_dict()}")
    print(f"ground truth: {truth.issue_type.value_counts().to_dict()}")
    print(f"attributed orders: {len(tables['attribution']):,} of {len(tables['order']):,}")
    print(f"attributed revenue: ${metrics['overall'].iloc[0]['attributed_revenue']:,.2f}")
    print(f"manifest: {out / 'manifest.json'} ({manifest['artifact_count']} artifacts)")
    print(
        f"VALIDATION {'PASS' if comparison['passed'] else 'FAIL'}: "
        f"{comparison['detected']}/{comparison['labelled']} labelled detected; "
        f"missed={len(comparison['missed'])}; "
        f"false_positives={len(comparison['false_positive'])}; "
        f"unexplained={len(comparison['unexplained'])}"
    )

    if a.repro_check:
        from cartrecovery.services.reproducibility import run_reproducibility
        proof = run_reproducibility(cfg, a.scale, seed, formats=formats)
        print(
            f"REPRODUCIBILITY {'PASS' if proof['reproducible'] else 'FAIL'}: "
            f"{proof['matched_files']}/{proof['file_count']} artifact checksums matched"
        )
        write_json_file(out / "reproducibility.json", proof)
        build_manifest(out, seed, a.scale, Path(__file__).resolve().parents[2])

    if a.validate and not comparison["passed"]:
        raise SystemExit(1)
    if a.repro_check and not proof["reproducible"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
