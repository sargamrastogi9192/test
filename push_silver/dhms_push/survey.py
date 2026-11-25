"""Which survey (UPC + phase + cycle) the silver files belong to, and how its rows are found in a table."""

import re

import pandas as pd

from . import config
from .project_ids import upc_key


def survey_of(frames: dict, folder: str) -> dict:
    """{'upc', 'phase', 'cycle', 'problems'} from the silver data (upc_code / upc, phase, cycle columns),
       with the folder path (Zone=… /Phase=… /Cycle=… /UPC=…) as a fallback. Every table must agree."""
    seen = {"upc": set(), "phase": set(), "cycle": set()}
    for df in frames.values():
        if df is None or df.empty:
            continue
        for col in config.UPC_COLUMNS:
            if col in df:
                seen["upc"] |= {str(v) for v in df[col].dropna().unique()}
                break
        for col in config.SURVEY_COLUMNS:
            if col in df:
                seen[col] |= {str(v).strip() for v in df[col].dropna().unique()}
    path = folder.replace("\\", "/")
    from_path = {"upc": (re.findall(r"UPC=([^/]+)", path) or [None])[-1],
                 "phase": (re.findall(r"Phase=(\d+)", path, re.I) or [None])[-1],
                 "cycle": (re.findall(r"Cycle=(\d+)", path, re.I) or [None])[-1]}
    out, problems = {}, []
    for k in ["upc", "phase", "cycle"]:
        vals = {v for v in seen[k] if v}
        keys = {upc_key(v) for v in vals} if k == "upc" else {re.sub(r"\D", "", v) or v for v in vals}
        if len(keys) > 1:
            problems.append(f"silver tables hold more than one {k}: {sorted(vals)}")
        out[k] = sorted(vals)[0] if vals else from_path[k]
        if out[k] is None:
            problems.append(f"{k} not found in the silver data or the folder path")
    if out["phase"] is not None:
        out["phase"] = re.sub(r"\D", "", str(out["phase"])) or out["phase"]
    if out["cycle"] is not None:
        out["cycle"] = re.sub(r"\D", "", str(out["cycle"])) or out["cycle"]
    out["problems"] = problems
    return out


def upc_underscore(upc) -> str:
    return re.sub(r"[-/\s]+", "_", str(upc).strip()).upper()


def scope_sql(table: str, db_columns: list, survey: dict, style: str = "sqlalchemy"):
    """(SQL condition, params) matching this survey's rows in a table, or None when the table has no UPC column.
       UPC compared without case / separators; phase and cycle as numbers in text, when the table has them.
       style 'sqlalchemy' → :name placeholders, 'psycopg2' → %(name)s."""
    ph = (lambda n: f":{n}") if style == "sqlalchemy" else (lambda n: f"%({n})s")
    ucol = next((c for c in config.UPC_COLUMNS if c in db_columns), None)
    if ucol is None:
        rule = config.SCOPE_BY_COLUMN.get(table)
        if rule and rule[0] in db_columns:
            return f'"{rule[0]}"::text = {ph("scope_value")}', {"scope_value": rule[1].format(upc=upc_underscore(survey["upc"]))}
        return None
    parts = [f"regexp_replace(lower(\"{ucol}\"::text), '[^0-9a-z]', '', 'g') = {ph('upc_key')}"]
    params = {"upc_key": upc_key(survey["upc"])}
    for c in config.SURVEY_COLUMNS:
        if c in db_columns and survey.get(c) is not None:
            parts.append(f"regexp_replace(\"{c}\"::text, '[^0-9]', '', 'g') = {ph(c)}")
            params[c] = str(survey[c])
    return " AND ".join(parts), params


def scope_note(table: str, db_columns: list) -> str:
    if not any(c in db_columns for c in config.UPC_COLUMNS) and table in config.SCOPE_BY_COLUMN:
        col, val = config.SCOPE_BY_COLUMN[table]
        return f"{col} = {val} (all rows of the UPC)"
    used = [c for c in config.UPC_COLUMNS if c in db_columns][:1] + [c for c in config.SURVEY_COLUMNS if c in db_columns]
    missing = [c for c in config.SURVEY_COLUMNS if c not in db_columns]
    return ", ".join(used) + (f" (no {', '.join(missing)} column: ALL rows of the UPC)" if missing else "")
