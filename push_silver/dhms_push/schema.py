"""How a table is defined in the database: columns (exact type, NOT NULL, default, identity / generated),
primary key, unique / foreign key / check constraints, indexes, triggers, enum values, comments.
Only catalog SELECTs."""

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Connection

# ── 1. Database definition ────────────────────────────────────────────────────
@dataclass
class TableDef:
    schema: str
    table: str
    exists: bool = False
    columns: list = field(default_factory=list)       # dicts, in table order
    primary_key: list = field(default_factory=list)
    uniques: list = field(default_factory=list)       # [{name, columns}]
    foreign_keys: list = field(default_factory=list)  # [{name, columns, ref_schema, ref_table, ref_columns, definition}]
    checks: list = field(default_factory=list)        # [{name, definition}]
    indexes: list = field(default_factory=list)       # [{name, definition}]
    triggers: list = field(default_factory=list)      # [{name, definition}]
    comment: str | None = None
    estimated_rows: int | None = None

    def column(self, name):
        return next((c for c in self.columns if c["name"] == name), None)


def _q(conn: Connection, sql: str, **params):
    return conn.execute(text(sql), params).mappings().all()


def read_table_def(conn: Connection, schema: str, table: str) -> TableDef:
    t = TableDef(schema, table)
    fq = f'"{schema}"."{table}"'
    oid = conn.execute(text("SELECT to_regclass(:fq)::oid"), {"fq": fq}).scalar()
    if oid is None:
        return t
    t.exists = True

    info = {r["column_name"]: r for r in _q(conn, """
        SELECT column_name, data_type, udt_schema, udt_name, character_maximum_length, numeric_precision,
               numeric_scale, is_nullable, column_default, is_identity, identity_generation, is_generated,
               generation_expression
        FROM information_schema.columns WHERE table_schema = :s AND table_name = :t""", s=schema, t=table)}
    for r in _q(conn, """
        SELECT a.attnum, a.attname, format_type(a.atttypid, a.atttypmod) AS full_type, ty.typtype,
               a.atttypid AS type_oid, col_description(a.attrelid, a.attnum) AS comment
        FROM pg_attribute a JOIN pg_type ty ON ty.oid = a.atttypid
        WHERE a.attrelid = :oid AND a.attnum > 0 AND NOT a.attisdropped ORDER BY a.attnum""", oid=oid):
        i = info.get(r["attname"], {})
        enum = None
        if r["typtype"] == "e":
            enum = [x["enumlabel"] for x in _q(conn, "SELECT enumlabel FROM pg_enum WHERE enumtypid = :o "
                                                     "ORDER BY enumsortorder", o=r["type_oid"])]
        t.columns.append({
            "position": int(r["attnum"]), "name": r["attname"], "type": r["full_type"],
            "data_type": i.get("data_type"), "udt_name": i.get("udt_name"),
            "max_length": i.get("character_maximum_length"),
            "numeric_precision": i.get("numeric_precision"), "numeric_scale": i.get("numeric_scale"),
            "nullable": i.get("is_nullable", "YES") == "YES", "default": i.get("column_default"),
            "identity": i.get("identity_generation") if i.get("is_identity") == "YES" else None,
            "generated": i.get("generation_expression") if i.get("is_generated") == "ALWAYS" else None,
            "enum_values": enum, "comment": r["comment"],
        })
    names = {c["position"]: c["name"] for c in t.columns}

    pg15 = (conn.execute(text("SELECT current_setting('server_version_num')::int")).scalar() or 0) >= 150000
    nnd_col = "x.indnullsnotdistinct" if pg15 else "false"
    nnd = {r["indexrelid"]: bool(r["nnd"]) for r in _q(
        conn, f"SELECT x.indexrelid, {nnd_col} AS nnd FROM pg_index x WHERE x.indrelid = :oid", oid=oid)}
    for r in _q(conn, """
        SELECT c.conname, c.contype, pg_get_constraintdef(c.oid) AS def, c.conkey, c.confkey,
               rn.nspname AS ref_schema, rc.relname AS ref_table, c.confrelid, c.conindid
        FROM pg_constraint c
        LEFT JOIN pg_class rc ON rc.oid = c.confrelid LEFT JOIN pg_namespace rn ON rn.oid = rc.relnamespace
        WHERE c.conrelid = :oid ORDER BY c.contype, c.conname""", oid=oid):
        cols = [names.get(k) for k in (r["conkey"] or [])]
        if r["contype"] == "p":
            t.primary_key = cols
        elif r["contype"] == "u":
            t.uniques.append({"name": r["conname"], "columns": cols,
                              "nulls_not_distinct": nnd.get(r["conindid"], False)})
        elif r["contype"] == "f":
            ref_names = {x["attnum"]: x["attname"] for x in _q(
                conn, "SELECT attnum, attname FROM pg_attribute WHERE attrelid = :o", o=r["confrelid"])}
            t.foreign_keys.append({"name": r["conname"], "columns": cols, "ref_schema": r["ref_schema"],
                                   "ref_table": r["ref_table"],
                                   "ref_columns": [ref_names.get(k) for k in (r["confkey"] or [])],
                                   "definition": r["def"]})
        elif r["contype"] == "c":
            t.checks.append({"name": r["conname"], "definition": r["def"]})
    t._constraint_defs = [(r["conname"], r["def"]) for r in _q(
        conn, "SELECT conname, pg_get_constraintdef(oid) AS def FROM pg_constraint WHERE conrelid = :oid "
              "ORDER BY contype, conname", oid=oid)]

    # unique indexes that are not constraints still reject duplicates
    for r in _q(conn, """
        SELECT i.relname AS name, x.indexrelid, pg_get_indexdef(x.indexrelid) AS def, x.indisunique, x.indisprimary,
               x.indkey::int2[] AS keys, x.indpred IS NOT NULL AS partial
        FROM pg_index x JOIN pg_class i ON i.oid = x.indexrelid WHERE x.indrelid = :oid ORDER BY i.relname""", oid=oid):
        t.indexes.append({"name": r["name"], "definition": r["def"]})
        keys = [names.get(k) for k in (r["keys"] or []) if k]
        if r["indisunique"] and not r["indisprimary"] and not r["partial"] and None not in keys \
                and not any(u["columns"] == keys for u in t.uniques):
            t.uniques.append({"name": r["name"], "columns": keys,
                              "nulls_not_distinct": nnd.get(r["indexrelid"], False)})
    t.triggers = [{"name": r["tgname"], "definition": r["def"]} for r in _q(
        conn, "SELECT tgname, pg_get_triggerdef(oid) AS def FROM pg_trigger WHERE tgrelid = :oid "
              "AND NOT tgisinternal ORDER BY tgname", oid=oid)]
    t.comment = conn.execute(text("SELECT obj_description(:oid, 'pg_class')"), {"oid": oid}).scalar()
    est = conn.execute(text("SELECT reltuples::bigint FROM pg_class WHERE oid = :oid"), {"oid": oid}).scalar()
    t.estimated_rows = None if est is None or est < 0 else int(est)
    return t


def create_table_sql(t: TableDef) -> str:
    """CREATE TABLE text rebuilt from the catalog (types, NOT NULL, defaults, constraints, indexes, triggers)."""
    if not t.exists:
        return f"-- {t.schema}.{t.table}: table not found in the database\n"
    lines = []
    for c in t.columns:
        s = f'    "{c["name"]}" {c["type"]}'
        if c["generated"]:
            s += f' GENERATED ALWAYS AS ({c["generated"]}) STORED'
        elif c["identity"]:
            s += f' GENERATED {c["identity"]} AS IDENTITY'
        elif c["default"] is not None:
            s += f' DEFAULT {c["default"]}'
        if not c["nullable"]:
            s += " NOT NULL"
        lines.append(s)
    lines += [f'    CONSTRAINT "{n}" {d}' for n, d in getattr(t, "_constraint_defs", [])]
    out = f'CREATE TABLE "{t.schema}"."{t.table}" (\n' + ",\n".join(lines) + "\n);\n"
    out += "".join(f"{i['definition']};\n" for i in t.indexes if not i["name"].endswith("_pkey"))
    out += "".join(f"{x['definition']};\n" for x in t.triggers)
    if t.comment:
        out += f"COMMENT ON TABLE \"{t.schema}\".\"{t.table}\" IS {t.comment!r};\n"
    out += "".join(f"COMMENT ON COLUMN \"{t.schema}\".\"{t.table}\".\"{c['name']}\" IS {c['comment']!r};\n"
                   for c in t.columns if c["comment"])
    return out


