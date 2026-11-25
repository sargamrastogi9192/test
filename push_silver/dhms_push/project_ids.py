"""Project ids of a UPC from the project master table of the database the push goes to.

The UPC is matched ignoring case and separators (N_01002_01001_GJ = N/01002/01001/GJ = n-01002-01001-gj):
first on the master's UPC column, then on the column that lists other spellings of the UPC.
"""

import re

import pandas as pd
from sqlalchemy import text

from . import config


def upc_key(v) -> str:
    return re.sub(r"[^0-9a-z]", "", str(v).lower())


def lookup_ids(conn, schema: str, table: str, upc: str) -> dict:
    """{'found': bool, 'how': str, 'ids': {column: value}, 'missing': [...], 'row': {...}}."""
    out = {"found": False, "how": "", "ids": {c: None for c in config.ID_COLUMNS}, "missing": [], "row": None}
    fq = f'"{schema}"."{table}"'
    if conn.execute(text("SELECT to_regclass(:fq)"), {"fq": fq}).scalar() is None:
        out["how"] = f"table {schema}.{table} not found in this database"
        return out
    df = pd.read_sql(text(f"SELECT * FROM {fq}"), conn)
    want = upc_key(upc)
    hit = pd.DataFrame()
    if config.MASTER_UPC_COLUMN in df:
        hit = df[df[config.MASTER_UPC_COLUMN].map(upc_key) == want]
        out["how"] = f"{table}.{config.MASTER_UPC_COLUMN}"
    if hit.empty and config.MASTER_ALT_UPC_COLUMN in df:
        alt = df[config.MASTER_ALT_UPC_COLUMN].fillna("").astype(str).map(
            lambda s: {upc_key(x) for x in re.split(r"[\n,;|]+", s) if x.strip()})
        hit = df[alt.map(lambda s: want in s)]
        out["how"] = f"{table}.{config.MASTER_ALT_UPC_COLUMN}"
    if hit.empty:
        out["how"] = f"UPC {upc} not in {table} ({len(df):,} rows)"
        return out
    if len(hit) > 1:
        out["how"] += f" ({len(hit)} rows match, the first is used)"
    row = hit.iloc[0]
    out["found"], out["row"] = True, {k: (None if pd.isna(v) else v) for k, v in row.items()}
    for col, src in config.ID_COLUMNS.items():
        if src not in df.columns:
            out["missing"].append(f"{col} (no column {src} in {table})")
            continue
        v = row[src]
        out["ids"][col] = None if pd.isna(v) else (int(v) if isinstance(v, float) and v.is_integer() else v)
        if out["ids"][col] is None:
            out["missing"].append(f"{col} (empty in {table})")
    return out


def apply_ids(table: str, df: pd.DataFrame, db_columns: list, ids: dict) -> tuple[pd.DataFrame, list]:
    """Write the master ids into every id column the DATABASE table has (added if silver lacks it).
       Returns (frame, filled column names). KEEP_SILVER_VALUE pairs keep the silver value."""
    df = df.copy()
    filled = []
    for col, v in ids.items():
        if col not in db_columns or (table, col) in config.KEEP_SILVER_VALUE or v is None:
            continue
        df[col] = v
        filled.append(col)
    return df, filled
