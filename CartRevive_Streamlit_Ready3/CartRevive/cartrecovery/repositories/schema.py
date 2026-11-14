"""Tier 3: builds SQLite DDL from the dictionary (so schema == docs), plus the separate ground-truth DB."""
from __future__ import annotations

import sqlite3

from cartrecovery.domain.dictionary import ENTITIES, ENUMS, GROUND_TRUTH_FIELDS

_ENUM_BY_ALLOWED = {f"enum {k}": v for k, v in ENUMS.items()}


def ddl() -> str:
    out = []
    for name, e in ENTITIES.items():
        cols, pk_multi = [], "," in e["pk"]
        for fname, typ, req, allowed, _u, uniq in e["fields"]:
            parts = [f'"{fname}"', typ]
            if req: parts.append("NOT NULL")
            if fname == e["pk"] and not pk_multi: parts.append("PRIMARY KEY")
            elif uniq: parts.append("UNIQUE")
            enum = _ENUM_BY_ALLOWED.get(allowed)
            if enum:
                parts.append(f'CHECK ("{fname}" IN ({",".join(repr(v) for v in enum)}))')
            cols.append(" ".join(parts))
        if pk_multi:
            cols.append(f"PRIMARY KEY ({e['pk']})")
        for col, ref in e["fks"].items():
            t, c = ref.split(".")
            cols.append(f'FOREIGN KEY ("{col}") REFERENCES "{t}"("{c}")')
        out.append(f'CREATE TABLE "{name}" (\n  ' + ",\n  ".join(cols) + "\n);")
        out.append(f'CREATE INDEX idx_{name}_fk ON "{name}"({", ".join(e["fks"]) or e["pk"].split(",")[0]});'
                   if e["fks"] else "")
    return "\n".join(s for s in out if s)


def gt_ddl() -> str:
    cols = ",\n  ".join(f'"{c}" TEXT' for c in GROUND_TRUTH_FIELDS)
    return f'CREATE TABLE "ground_truth" (\n  {cols},\n  PRIMARY KEY ("gt_id")\n);'


def create_db(path: str = ":memory:") -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.execute("PRAGMA foreign_keys=ON")
    con.executescript(ddl())
    return con


def create_gt_db(path: str = ":memory:") -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.executescript(gt_ddl())
    return con
