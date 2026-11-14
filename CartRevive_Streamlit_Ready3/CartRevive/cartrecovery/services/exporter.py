"""Deterministic multi-format exports and SHA-256 manifests."""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from cartrecovery.domain.dictionary import ENTITIES
from cartrecovery.repositories.schema import create_db, ddl


SUPPORTED_FORMATS = {"csv", "json", "sql", "sqlite"}


def _write_lf(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # newline="" prevents platform translation; content is explicitly LF-terminated.
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(content.replace("\r\n", "\n").replace("\r", "\n"))


def _json_value(value):
    if value is None or value is pd.NA:
        return None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        value = float(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    if isinstance(value, np.bool_):
        return bool(value)
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def _json_records(df: pd.DataFrame) -> str:
    rows = [
        {str(k): _json_value(v) for k, v in row.items()}
        for row in df.to_dict(orient="records")
    ]
    return json.dumps(rows, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def _sql_literal(value) -> str:
    value = _json_value(value)
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    return "'" + str(value).replace("'", "''") + "'"


def _sql_seed(tables: dict[str, pd.DataFrame]) -> str:
    statements = [
        "-- CartRevive deterministic SQL seed; ground truth is intentionally excluded.",
        "PRAGMA foreign_keys=ON;",
        "BEGIN TRANSACTION;",
        ddl(),
    ]
    for table in ENTITIES:
        if table not in tables:
            raise ValueError(f"Missing schema table: {table}")
        df = tables[table]
        if df.empty:
            continue
        cols = list(df.columns)
        quoted = ", ".join(f'"{c}"' for c in cols)
        for row in df.itertuples(index=False, name=None):
            values = ", ".join(_sql_literal(v) for v in row)
            statements.append(f'INSERT INTO "{table}" ({quoted}) VALUES ({values});')
    statements.extend(["COMMIT;", ""])
    return "\n".join(statements)


def _sqlite_seed(path: Path, tables: dict[str, pd.DataFrame]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    con = create_db(str(path))
    try:
        for table in ENTITIES:
            if table not in tables:
                raise ValueError(f"Missing schema table: {table}")
            # to_sql appends to the dictionary-generated schema; index is never exported.
            tables[table].to_sql(table, con, if_exists="append", index=False, method=None)
        con.commit()
        con.execute("PRAGMA optimize")
        con.commit()
    finally:
        con.close()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()



def write_json_file(path: str | Path, value) -> None:
    """Write deterministic JSON with explicit LF-only line endings."""
    _write_lf(Path(path), json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


def _row_count(path: Path):
    suffix = path.suffix.lower()
    if suffix == ".csv":
        # Generated CSV fields are single-line values. Count LF bytes in chunks
        # instead of loading every large file back into pandas just for a manifest.
        with path.open("rb") as handle:
            lines = sum(chunk.count(b"\n") for chunk in iter(lambda: handle.read(1024 * 1024), b""))
        return max(0, int(lines - 1))
    # JSON row counts are intentionally omitted: parsing multi-hundred-MB JSON
    # artifacts just to populate optional manifest metadata is prohibitively slow.
    return None


def build_manifest(
    output_dir: str | Path,
    seed: int,
    scale: float,
    source_root: str | Path | None = None,
) -> dict:
    """Write a stable manifest; it intentionally does not checksum itself."""
    root = Path(output_dir).resolve()
    source = Path(source_root).resolve() if source_root else None
    artifacts = []
    for path in sorted(p for p in root.rglob("*") if p.is_file() and p.name != "manifest.json"):
        rel = path.relative_to(root).as_posix()
        artifacts.append({
            "path": rel,
            "row_count": _row_count(path),
            "size_bytes": path.stat().st_size,
            "sha256": _sha256(path),
        })

    sources = []
    if source and source.exists():
        for path in sorted(source.rglob("*")):
            rel_parts = path.relative_to(source).parts
            if not path.is_file() or any(part in {".git", ".venv", "__pycache__", ".pytest_cache", "output", "build", "dist"} for part in rel_parts):
                continue
            if path.resolve().is_relative_to(root):
                continue
            sources.append({
                "path": path.relative_to(source).as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": _sha256(path),
            })

    manifest = {
        "project": "CartRevive",
        "manifest_version": 1,
        "seed": int(seed),
        "scale": float(scale),
        "artifact_count": len(artifacts),
        "source_file_count": len(sources),
        "artifacts": artifacts,
        "source_files": sources,
    }
    _write_lf(root / "manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    return manifest


def export_datasets(
    tables: dict[str, pd.DataFrame],
    output_dir: str | Path,
    formats: Iterable[str] = ("csv", "json", "sql", "sqlite"),
    ground_truth: pd.DataFrame | None = None,
    metrics: dict[str, pd.DataFrame] | None = None,
    seed: int = 20261022,
    scale: float = 1.0,
    source_root: str | Path | None = None,
) -> dict:
    """Export all source tables and optional reports, then create the manifest."""
    selected = {str(x).strip().lower() for x in formats}
    unknown = selected - SUPPORTED_FORMATS
    if unknown:
        raise ValueError(f"Unsupported export format(s): {', '.join(sorted(unknown))}")
    if not selected:
        raise ValueError("At least one export format is required")

    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    if "csv" in selected:
        data_dir = root / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        for name, df in tables.items():
            df.to_csv(data_dir / f"{name}.csv", index=False, lineterminator="\n", encoding="utf-8")
    if "json" in selected:
        json_dir = root / "json"
        json_dir.mkdir(parents=True, exist_ok=True)
        for name, df in tables.items():
            _write_lf(json_dir / f"{name}.json", _json_records(df))
    if "sql" in selected:
        _write_lf(root / "seed.sql", _sql_seed(tables))
    if "sqlite" in selected:
        _sqlite_seed(root / "cartrevive.db", tables)

    # Ground truth and reports remain separate from the 13 application input tables.
    if ground_truth is not None:
        if "csv" in selected:
            ground_truth.to_csv(root / "ground_truth.csv", index=False, lineterminator="\n", encoding="utf-8")
        if "json" in selected:
            _write_lf(root / "ground_truth.json", _json_records(ground_truth))
    if metrics:
        report_dir = root / "metrics"
        report_dir.mkdir(parents=True, exist_ok=True)
        for name, df in metrics.items():
            if name == "overall":
                _write_lf(report_dir / "overall.json", _json_records(df))
            else:
                df.to_csv(report_dir / f"{name}.csv", index=False, lineterminator="\n", encoding="utf-8")

    return build_manifest(root, seed, scale, source_root)
