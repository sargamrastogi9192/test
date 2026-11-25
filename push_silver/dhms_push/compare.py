"""Silver frame vs a table definition, before the push. Severity: BLOCKER = the insert would fail,
WARNING = it would insert but not as expected, INFO = for information."""

import json
import math
import re

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Connection

from . import config
from .schema import TableDef

INT_RANGES = {"smallint": 2**15 - 1, "integer": 2**31 - 1, "bigint": 2**63 - 1}
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}$")
TRUE_FALSE = {"true", "false", "t", "f", "yes", "no", "y", "n", "1", "0", "on", "off"}
MAX_EXAMPLES = 3
KEY_CHUNK = 5000


# ── 2. Silver vs definition ───────────────────────────────────────────────────
def _is_null(v) -> bool:
    if v is None:
        return True
    if isinstance(v, (list, tuple, dict, np.ndarray)):
        return False
    try:
        return bool(pd.isna(v))
    except (TypeError, ValueError):
        return False


def _num(v):
    if isinstance(v, (bool, np.bool_)):
        return float(v)
    if isinstance(v, (int, float, np.integer, np.floating)):
        return float(v)
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _value_problem(v, c: dict) -> str | None:
    """Why value v does not fit column c, else None."""
    dt, udt = (c["data_type"] or "").lower(), (c["udt_name"] or "").lower()
    is_list = isinstance(v, (list, tuple, np.ndarray))
    if dt == "array":
        return None                    # lists as they are; a single value becomes a one-element array (WARNING)
    if is_list or isinstance(v, dict):
        if dt in ("json", "jsonb"):
            return None
        if is_list and dt in ("character varying", "character", "text"):
            n = c.get("max_length")
            joined = config.LIST_SEPARATOR.join(str(x) for x in v)
            return f"longer than {n} characters" if n and len(joined) > int(n) else None
        return f"{type(v).__name__} value for a {c['type']} column"
    if dt in INT_RANGES:
        x = _num(v)
        if x is None or math.isnan(x) or math.isinf(x):
            return "not a number"
        if x != int(x):
            return "has decimals"
        return "out of range" if abs(x) > INT_RANGES[dt] else None
    if dt in ("numeric", "decimal"):
        x = _num(v)
        if x is None or math.isinf(x):
            return "not a number"
        p, s = c.get("numeric_precision"), c.get("numeric_scale") or 0
        if p and abs(round(x, int(s))) >= 10 ** (int(p) - int(s)):
            return f"too large for {c['type']}"
        return None
    if dt in ("real", "double precision"):
        return None if _num(v) is not None else "not a number"
    if dt == "boolean":
        if isinstance(v, (bool, np.bool_)):
            return None
        return None if str(v).strip().lower() in TRUE_FALSE else "not true/false"
    if dt == "date" or dt.startswith("timestamp") or dt.startswith("time"):
        if isinstance(v, (pd.Timestamp, np.datetime64)) or hasattr(v, "year"):
            return None
        if dt.startswith("time") and not dt.startswith("timestamp"):
            return None
        return None if pd.notna(pd.to_datetime(str(v), errors="coerce")) else "not a date/time"
    if dt in ("json", "jsonb"):
        if not isinstance(v, str):
            return None
        try:
            json.loads(v)              # NaN / Infinity are accepted here and reported as a WARNING
            return None
        except ValueError:
            return "not valid JSON"
    if dt in ("character varying", "character", "text"):
        n = c.get("max_length")
        return f"longer than {n} characters" if n and len(str(v)) > int(n) else None
    if dt == "uuid":
        return None if UUID_RE.match(str(v)) else "not a UUID"
    if dt == "user-defined" and c.get("enum_values"):
        return None if str(v) in c["enum_values"] else f"not one of {c['enum_values']}"
    return None


def _has_nan(s: str) -> bool:
    found = []
    try:
        json.loads(s, parse_constant=lambda x: found.append(x))
    except ValueError:
        return False
    return bool(found)


def _not_checked(c: dict) -> bool:
    dt = (c["data_type"] or "").lower()
    known = set(INT_RANGES) | {"numeric", "decimal", "real", "double precision", "boolean", "date", "json", "jsonb",
                               "character varying", "character", "text", "uuid", "array"}
    return not (dt in known or dt.startswith("timestamp") or dt.startswith("time")
                or (dt == "user-defined" and c.get("enum_values")))


def _short(v, n=60):
    s = str(v.tolist() if isinstance(v, np.ndarray) else v)
    return s if len(s) <= n else s[:n - 3] + "..."


def _key(v):
    """Hashable, comparable form of a key value."""
    if isinstance(v, (list, tuple, np.ndarray)):
        return tuple(_key(x) for x in v)
    if isinstance(v, (float, np.floating)) and float(v).is_integer():
        return int(v)
    if isinstance(v, (np.integer,)):
        return int(v)
    return v


def compare(t: TableDef, df: pd.DataFrame | None, conn: Connection | None, db_checks: bool = True,
            scope=None, mode: str = "replace"):
    """Returns (column rows, issue rows). Issue severity: BLOCKER = the insert would fail;
    WARNING = it would insert but the data would not be what you expect; INFO = for information.
    scope = (SQL condition, params) selecting this survey's rows in the table (see survey.scope_sql);
    mode 'replace' deletes those rows before the insert, 'append' keeps them."""
    cols, issues = [], []

    def issue(sev, column, what, n=None, examples=None):
        issues.append({"table": t.table, "severity": sev, "column": column, "issue": what,
                       "rows": n, "examples": "; ".join(_short(x) for x in (examples or []))})

    if df is None:
        issue("INFO", "", "no silver file for this table (this pipeline does not write it) → skipped")
        return cols, issues
    if not t.exists:
        issue("BLOCKER", "", "table does not exist in the database")
        return [{"table": t.table, "column": c["name"], "db_type": c["type"], "db_nullable": c["nullable"],
                 "db_default": c["default"], "in_silver": False} for c in t.columns], issues

    n = len(df)
    nulls = {c: df[c].map(_is_null) for c in df.columns}
    for c in t.columns:
        name = c["name"]
        row = {"table": t.table, "column": name, "db_type": c["type"], "db_nullable": c["nullable"],
               "db_default": c["default"], "db_identity": c["identity"], "db_generated": bool(c["generated"]),
               "in_silver": name in df.columns}
        if name not in df.columns:
            if c["generated"] or c["identity"] or c["default"] is not None:
                row["status"] = "filled by the database"
            elif not c["nullable"]:
                row["status"] = "BLOCKER: NOT NULL, no default, not in silver"
                issue("BLOCKER", name, "NOT NULL column with no default is missing in silver", n)
            else:
                row["status"] = "missing in silver → NULL"
                issue("WARNING", name, "column missing in silver → every row gets NULL", n)
            cols.append(row)
            continue
        s = df[name]
        nn = s[~nulls[name]]
        row.update({"silver_dtype": str(s.dtype), "silver_nulls": int(nulls[name].sum()),
                    "silver_example": _short(nn.iloc[0]) if len(nn) else None})
        problems = []
        if c["generated"]:
            problems.append("generated column: silver value must not be sent")
            issue("BLOCKER", name, "GENERATED ALWAYS column: the database computes it, silver must not send it", n)
        if c["identity"] == "ALWAYS":
            problems.append("identity ALWAYS: silver value rejected")
            issue("BLOCKER", name, "GENERATED ALWAYS AS IDENTITY: an explicit value is rejected "
                                   "(drop the column from the push or use OVERRIDING SYSTEM VALUE)", n)
        if not c["nullable"] and row["silver_nulls"]:
            if c["default"] is not None:
                issue("WARNING", name, f"NULL in silver → the database default {c['default']} is written",
                      row["silver_nulls"])
            else:
                problems.append(f"{row['silver_nulls']} NULL in NOT NULL column")
                issue("BLOCKER", name, "NULL values in a NOT NULL column without a default", row["silver_nulls"])
        if _not_checked(c):
            row["type_check"] = f"not checked ({c['type']})"
            issue("INFO", name, f"values not checked against type {c['type']}")
        else:
            bad = [(v, p) for v in nn for p in [_value_problem(v, c)] if p]
            row["type_check"] = "ok" if not bad else f"{len(bad)} bad"
            if bad:
                reasons = pd.Series([p for _, p in bad]).value_counts()
                for why, k in reasons.items():
                    ex = [v for v, p in bad if p == why][:MAX_EXAMPLES]
                    issue("BLOCKER", name, f"value does not fit {c['type']}: {why}", int(k), ex)
                problems.append(f"{len(bad)} values do not fit {c['type']}")
            dt = (c["data_type"] or "").lower()
            if dt in ("numeric", "decimal") and c.get("numeric_scale") is not None and len(nn):
                sc = int(c["numeric_scale"])
                x = pd.to_numeric(nn, errors="coerce").dropna()
                lost = int((x.round(sc) != x).sum())
                if lost:
                    issue("WARNING", name, f"{c['type']} keeps {sc} decimals: values will be rounded", lost,
                          x[x.round(sc) != x].head(MAX_EXAMPLES).tolist())
            if dt == "date" and len(nn):
                ts = pd.to_datetime(nn.astype(str), errors="coerce").dropna()
                cut = int((ts != ts.dt.normalize()).sum())
                if cut:
                    issue("WARNING", name, "date column: the time part will be dropped", cut,
                          nn.head(MAX_EXAMPLES).tolist())
        if (c["data_type"] or "").lower() == "array":
            single = [v for v in nn if not isinstance(v, (list, tuple, np.ndarray))]
            if single:
                issue("WARNING", name, "single value (not a list) → stored as a one-element array", len(single),
                      single[:MAX_EXAMPLES])
        if (c["data_type"] or "").lower() in ("json", "jsonb"):
            bad_const = [v for v in nn if isinstance(v, str) and _has_nan(v)]
            if bad_const:
                issue("WARNING", name, "NaN / Infinity inside the JSON (not valid for Postgres) → stored as null",
                      len(bad_const), bad_const[:MAX_EXAMPLES])
        if (c["data_type"] or "").lower() in ("character varying", "character", "text"):
            lists = [v for v in nn if isinstance(v, (list, tuple, np.ndarray))]
            if lists:
                issue("WARNING", name, f"silver lists stored as text joined with '{config.LIST_SEPARATOR}'",
                      len(lists), lists[:MAX_EXAMPLES])
        row["status"] = "; ".join(problems) if problems else "ok"
        cols.append(row)

    db_names = {c["name"] for c in t.columns}
    extra = [c for c in df.columns if c not in db_names]
    for c in extra:
        cols.append({"table": t.table, "column": c, "in_silver": True, "silver_dtype": str(df[c].dtype),
                     "status": "silver only: not in the database table (would not be pushed)"})
    if extra:
        issue("WARNING", ", ".join(extra), "silver columns not in the database table (dropped on push)", len(extra))

    # keys inside the silver file
    for kind, keys, nnd in ([("primary key", t.primary_key, True)] +
                            [(f"unique {u['name']}", u["columns"], u.get("nulls_not_distinct", False))
                             for u in t.uniques]):
        if not keys:
            continue
        missing = [k for k in keys if k not in df.columns]
        if missing:
            if kind == "primary key" and all(t.column(k) and (t.column(k)["identity"] or t.column(k)["default"])
                                             for k in missing):
                continue
            issue("BLOCKER" if kind == "primary key" else "WARNING", ", ".join(keys),
                  f"{kind} column(s) {missing} not in silver")
            continue
        k = df[keys].map(_key) if hasattr(df, "map") else df[keys].applymap(_key)
        if not nnd:   # Postgres: a key with a NULL part never equals another (unless NULLS NOT DISTINCT)
            k = k[~df[keys].map(_is_null).any(axis=1)]
        dup = int(k.duplicated(keep=False).sum())
        if dup:
            issue("BLOCKER", ", ".join(keys), f"duplicate {kind} values inside the silver file", dup,
                  ["|".join("" if v is None or (isinstance(v, float) and v != v) else str(v) for v in row)
                   for row in k[k.duplicated(keep=False)].head(MAX_EXAMPLES).itertuples(index=False)])

    if t.primary_key is not None and not t.primary_key:
        issue("INFO", "", "table has no primary key: re-pushing the same UPC would add duplicate rows")

    if not (db_checks and conn is not None):
        return cols, issues
    fq = f'"{t.schema}"."{t.table}"'

    # foreign keys: silver values that the referenced table does not have
    for fk in t.foreign_keys:
        if len(fk["columns"]) != 1 or fk["columns"][0] not in df.columns:
            if all(c in df.columns for c in fk["columns"]):
                issue("INFO", ", ".join(fk["columns"]), f"multi-column foreign key {fk['name']} not checked")
            continue
        col, ref = fk["columns"][0], fk["ref_columns"][0]
        vals = sorted({_key(v) for v in df[col] if not _is_null(v)}, key=str)
        found = set()
        for i in range(0, len(vals), KEY_CHUNK):
            part = vals[i:i + KEY_CHUNK]
            found |= {_key(r[0]) for r in conn.execute(text(
                f'SELECT DISTINCT "{ref}" FROM "{fk["ref_schema"]}"."{fk["ref_table"]}" WHERE "{ref}"::text = ANY(:v)'),
                {"v": [str(x) for x in part]})}
        miss = [v for v in vals if v not in found and str(v) not in {str(f) for f in found}]
        if miss:
            rows = int(df[col].map(lambda v: not _is_null(v) and (_key(v) in miss or str(_key(v)) in map(str, miss))).sum())
            issue("BLOCKER", col, f"foreign key {fk['name']}: value not in {fk['ref_table']}.{ref}", rows,
                  miss[:MAX_EXAMPLES])

    # rows of this survey already in the table, and primary keys that would collide
    if scope is None:
        issue("WARNING", "", "table has no UPC column: rows of this survey cannot be found (replace deletes nothing)")
        where, params = "FALSE", {}
    else:
        where, params = scope
        cnt = conn.execute(text(f"SELECT count(*) FROM {fq} WHERE {where}"), params).scalar()
        if mode == "replace":
            issue("INFO", "", f"rows of this survey already in the table: {cnt:,} → deleted before the insert", cnt)
        elif cnt:
            issue("WARNING", "", f"append: {cnt:,} rows of this survey are already in the table and stay "
                                 f"(the push adds {n:,} more)", cnt)
    for kind, keys in [("primary key", t.primary_key)] + [(f"unique {u['name']}", u["columns"]) for u in t.uniques]:
        if len(keys) != 1 or keys[0] not in df.columns:
            continue
        k = keys[0]
        vals = [str(_key(v)) for v in df[k] if not _is_null(v)]
        outside = f"NOT ({where})" if mode == "replace" else "TRUE"
        hit = 0
        for i in range(0, len(vals), KEY_CHUNK):
            hit += conn.execute(text(f'SELECT count(*) FROM {fq} WHERE "{k}"::text = ANY(:v) AND {outside}'),
                                {**params, "v": vals[i:i + KEY_CHUNK]}).scalar()
        if hit:
            issue("BLOCKER", k, f"{kind} values already in the table" +
                  (" on rows of ANOTHER survey (not deleted by replace)" if mode == "replace" else ""), hit)
    return cols, issues
