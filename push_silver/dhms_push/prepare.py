"""Silver frame → rows for INSERT, in the database table's own columns and types.

Only the table's columns are sent; columns the database fills itself (identity, generated, and those with a
default that silver does not have) are left out. Values are turned into the Python type psycopg2 sends for
the column type: int, float, bool, date, datetime, JSON, list (array) or text. Blank → NULL.
"""

import json
import math
from datetime import date, datetime

import numpy as np
import pandas as pd
from psycopg2.extensions import AsIs
from psycopg2.extras import Json

from . import config
from .schema import TableDef

_INT = ("smallint", "integer", "bigint")
_FLOAT = ("numeric", "decimal", "real", "double precision")
_TEXT = ("character varying", "character", "text")


def _blank(v) -> bool:
    if v is None:
        return True
    if isinstance(v, (list, tuple, dict, np.ndarray)):
        return False
    try:
        return bool(pd.isna(v))
    except (TypeError, ValueError):
        return False


def _plain(v):
    """numpy scalars / arrays → plain Python."""
    if isinstance(v, np.ndarray):
        return [_plain(x) for x in v.tolist()]
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    if isinstance(v, np.generic):
        return v.item()
    return v


def json_safe(v):
    """NaN / Infinity are not valid JSON for Postgres (Python writes them) → null."""
    if isinstance(v, float):
        return None if (math.isnan(v) or math.isinf(v)) else v
    if isinstance(v, dict):
        return {k: json_safe(x) for k, x in v.items()}
    if isinstance(v, list):
        return [json_safe(x) for x in v]
    return _plain(v) if isinstance(v, np.generic) else v


def convert(v, c: dict):
    if _blank(v):
        return None
    v = _plain(v)
    dt = (c["data_type"] or "").lower()
    if dt in _INT:
        return int(round(float(v))) if not isinstance(v, bool) else int(v)
    if dt in _FLOAT:
        x = float(v)
        return None if math.isnan(x) else x
    if dt == "boolean":
        return v if isinstance(v, bool) else str(v).strip().lower() in ("true", "t", "yes", "y", "1", "on")
    if dt == "date":
        return pd.Timestamp(v).date() if not isinstance(v, date) or isinstance(v, datetime) else v
    if dt.startswith("timestamp"):
        return pd.Timestamp(v).to_pydatetime()
    if dt in ("json", "jsonb"):
        if isinstance(v, str):
            try:
                v = json.loads(v)
            except ValueError:
                return v                   # the check reports it; the database rejects it
        return Json(json_safe(v))
    if dt == "array":
        if isinstance(v, list):
            return v
        t = str(v).strip()
        if t.startswith("[") and t.endswith("]"):          # '["NSV"]' / "['NSV']" written as text
            try:
                return list(json.loads(t.replace("'", '"')))
            except ValueError:
                pass
        if t.startswith("{") and t.endswith("}"):          # Postgres array literal: the database parses it
            return t
        return [v]                                         # one value → one-element array
    if dt in _TEXT:
        if isinstance(v, list):
            return config.LIST_SEPARATOR.join("" if x is None else str(x) for x in v)
        if isinstance(v, dict):
            return json.dumps(v)
        if isinstance(v, (pd.Timestamp, datetime)):
            return v.isoformat(sep=" ")
        return str(v)
    return str(v) if not isinstance(v, (list, dict)) else v


def insert_columns(t: TableDef, df: pd.DataFrame) -> list:
    """The table's columns to INSERT: those silver has, except generated / identity-ALWAYS columns."""
    return [c["name"] for c in t.columns
            if c["name"] in df.columns and not c["generated"] and c["identity"] != "ALWAYS"]


def rows_for_insert(t: TableDef, df: pd.DataFrame) -> tuple[list, list]:
    cols = insert_columns(t, df)
    defs = [t.column(c) for c in cols]
    # NULL in a NOT NULL column that has a default → the database default (an explicit NULL would be rejected)
    use_default = [(not d["nullable"]) and d["default"] is not None for d in defs]
    default = AsIs("DEFAULT")
    data = [tuple(default if (u and x is None) else x
                  for x, u in zip((convert(v, d) for v, d in zip(rec, defs)), use_default))
            for rec in df[cols].itertuples(index=False, name=None)]
    return cols, data
