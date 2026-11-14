"""Two-run, fixed-seed artifact checksum proof."""
from __future__ import annotations

import tempfile
from pathlib import Path

from cartrecovery.services.pipeline import build_dataset


def run_reproducibility(
    cfg: dict,
    scale: float = 0.02,
    seed: int | None = None,
    formats=("csv", "json", "sql", "sqlite"),
) -> dict:
    """Execute the full clean->metrics->anomaly->export workflow twice."""
    from cartrecovery.services.anomalies import inject
    from cartrecovery.services.exporter import export_datasets
    from cartrecovery.services.metrics import compute_metrics, validate_metrics

    actual_seed = cfg["seed"] if seed is None else seed
    manifests = []
    with tempfile.TemporaryDirectory(prefix="cartrevive-repro-") as tmp:
        root = Path(tmp)
        for run in ("run_a", "run_b"):
            clean = build_dataset(cfg, scale, actual_seed)
            metrics = compute_metrics(clean)
            validate_metrics(metrics)
            tables, truth = inject(cfg, clean, scale)
            manifests.append(export_datasets(
                tables, root / run, formats=formats, ground_truth=truth,
                metrics=metrics, seed=actual_seed, scale=scale,
            ))

    first = {x["path"]: x["sha256"] for x in manifests[0]["artifacts"]}
    second = {x["path"]: x["sha256"] for x in manifests[1]["artifacts"]}
    paths = sorted(set(first) | set(second))
    mismatches = [
        {"path": path, "run_a": first.get(path), "run_b": second.get(path)}
        for path in paths if first.get(path) != second.get(path)
    ]
    return {
        "seed": int(actual_seed),
        "scale": float(scale),
        "file_count": len(paths),
        "matched_files": len(paths) - len(mismatches),
        "reproducible": not mismatches and first.keys() == second.keys(),
        "mismatches": mismatches,
        "checksums": first if not mismatches else {},
    }
