"""Push the silver tables of ONE survey (UPC + phase + cycle) into the DHMS database.

    python push.py --silver <UPC folder or its dlm_kpi_new folder, local or s3://…>             # dry run
    python push.py --silver … --db dhms_staging                                                # another database
    python push.py --silver … --apply                                                          # really write
    python push.py --silver … --apply --mode append --tables master_pothole,geometry_parent

What happens:
 1. Reads the silver files of the survey and how every target table is defined in the database.
 2. Looks the UPC up in the project master (MASTER_PROJECT_TABLE, default master_upc_project) OF THE SAME
    DATABASE and writes its ids (project_id, region_id, piu_id, state_id, zone_id, contractor_id, ae_ie_id)
    into every table that has those columns — so the ids always belong to the database you push to.
 3. Checks every table against the silver data (BLOCKER / WARNING / INFO) and writes a report.
 4. Only with --apply and no BLOCKER: one transaction for all tables —
       replace (default): delete this survey's rows (UPC + phase + cycle) in each table, then insert;
       append:            insert only.
    Any error → everything is rolled back, the database is unchanged.

Without --apply nothing is written (the check runs in a READ ONLY transaction).
Report: reports/<time>_<db>_<UPC>/report.md (+ summary.csv, issues.csv, columns.csv, ddl/, push_log.csv).
Exit code: 0 ok, 1 blockers / push failed, 2 cannot connect or no silver files.
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime

import pandas as pd
from psycopg2.extras import execute_values
from sqlalchemy import text

from dhms_push import config
from dhms_push.compare import compare
from dhms_push.prepare import rows_for_insert
from dhms_push.project_ids import apply_ids, lookup_ids
from dhms_push.schema import create_table_sql, read_table_def
from dhms_push.silver import read_silver, silver_folder
from dhms_push.survey import scope_note, scope_sql, survey_of


def main() -> int:
    ap = argparse.ArgumentParser(description="Push silver tables of one survey into the DHMS database")
    ap.add_argument("--silver", required=True, help="UPC folder or its dlm_kpi_new folder (local or s3://)")
    ap.add_argument("--db", help="Database name (overrides DB_NAME in .env)")
    ap.add_argument("--schema", help="Schema (overrides DB_SCHEMA in .env)")
    ap.add_argument("--tables", help="Comma-separated database table names (default: all in dhms_push/config.py)")
    ap.add_argument("--mode", choices=["replace", "append"], default="replace",
                    help="replace (default): delete this survey's rows first; append: insert only")
    ap.add_argument("--apply", action="store_true", help="Really write. Without it: dry run, nothing is written")
    ap.add_argument("--out", help="Report folder (default reports/<time>_<db>_<UPC>)")
    a = ap.parse_args()

    db = config.load_db_settings(a.db, a.schema)
    wanted = {t.strip() for t in a.tables.split(",")} if a.tables else None
    targets = [t for t in config.TARGETS if not wanted or t[0] in wanted]
    if wanted and len(targets) != len(wanted):
        print(f"Unknown table(s): {sorted(wanted - {t[0] for t in config.TARGETS})} (see dhms_push/config.py)")
        return 2

    folder = silver_folder(a.silver)
    frames, paths = {}, {}
    for table, name, _ in targets:
        frames[table], paths[table] = read_silver(folder, name)
    if not any(df is not None for df in frames.values()):
        print(f"No silver files found in {folder}")
        return 2
    survey = survey_of(frames, folder)
    tag = f"{survey['upc'] or 'UPC'}".replace("/", "_")
    out = a.out or os.path.join("reports", f"{datetime.now():%Y%m%d_%H%M%S}_{db.name}_{tag}")
    os.makedirs(os.path.join(out, "ddl"), exist_ok=True)

    print(f"{'APPLY' if a.apply else 'DRY RUN'} | database {db.name} | schema {db.schema} | host {db.host} | mode {a.mode}")
    print(f"Silver  {folder}")
    print(f"Survey  UPC {survey['upc']} | phase {survey['phase']} | cycle {survey['cycle']}")
    engine = config.get_engine(db)
    try:
        conn = engine.connect()
        conn.execute(text("SET TRANSACTION READ ONLY"))
    except Exception as e:
        print(f"Could not connect: {e}")
        return 2

    summary, all_cols, all_issues, defs, prepared = [], [], [], {}, {}
    try:
        ids = lookup_ids(conn, db.schema, db.master_table, survey["upc"] or "")
        print(f"Ids     {'found' if ids['found'] else 'NOT FOUND'} via {ids['how']}: "
              + ", ".join(f"{k}={v}" for k, v in ids["ids"].items()))
        run_issues = [{"table": "(survey)", "severity": "BLOCKER", "column": "", "issue": p}
                      for p in survey["problems"]]
        if not ids["found"]:
            run_issues.append({"table": "(ids)", "severity": "BLOCKER", "column": "",
                               "issue": f"project ids: {ids['how']} — the id columns would stay NULL"})
        run_issues += [{"table": "(ids)", "severity": "WARNING", "column": m.split(" ")[0],
                        "issue": f"project id not available: {m}"} for m in ids["missing"]]
        all_issues += run_issues

        for table, name, group in targets:
            t = read_table_def(conn, db.schema, table)
            defs[table] = t
            with open(os.path.join(out, "ddl", f"{table}.sql"), "w", encoding="utf-8") as f:
                f.write(create_table_sql(t))
            df = frames[table]
            filled = []
            if df is not None and t.exists:
                df, filled = apply_ids(table, df, [c["name"] for c in t.columns], ids["ids"])
                prepared[table] = df
            scope = scope_sql(table, [c["name"] for c in t.columns], survey) if t.exists else None
            cols, issues = compare(t, df, conn, scope=scope, mode=a.mode)
            all_cols += cols
            all_issues += issues
            existing = None
            if t.exists and scope is not None:
                existing = conn.execute(text(f'SELECT count(*) FROM "{db.schema}"."{table}" WHERE {scope[0]}'),
                                        scope[1]).scalar()
            sev = pd.Series([i["severity"] for i in issues], dtype=object)
            s = {"group": group, "table": table, "silver_file": name if name != table else "",
                 "exists_in_db": t.exists, "primary_key": ", ".join(t.primary_key),
                 "silver_rows": None if df is None else len(df),
                 "rows_of_survey_in_db": existing,
                 "to_delete": (existing or 0) if (a.mode == "replace" and df is not None) else 0,
                 "to_insert": 0 if df is None else len(df),
                 "survey_rows_found_by": scope_note(table, [c["name"] for c in t.columns]) if t.exists else "",
                 "ids_filled": ", ".join(filled),
                 "blockers": int((sev == "BLOCKER").sum()), "warnings": int((sev == "WARNING").sum()),
                 "silver_path": paths[table]}
            summary.append(s)
            state = ("skipped (no silver file)" if df is None else "TABLE NOT IN DB" if not t.exists else
                     "ok" if not s["blockers"] else f"{s['blockers']} BLOCKER(S)")
            print(f"  {table:28s} {state:24s}" + ("" if df is None else
                  f" insert {s['to_insert']:7,} | delete {s['to_delete']:7,} | warnings {s['warnings']}"))
    finally:
        conn.rollback()
        conn.close()

    isu = pd.DataFrame(all_issues, columns=["table", "severity", "column", "issue", "rows", "examples"])
    blockers = int((isu["severity"] == "BLOCKER").sum())
    push_log, status = [], "dry run — nothing written"
    if a.apply and blockers:
        status = f"NOT PUSHED: {blockers} blocker(s)"
    elif a.apply:
        status, push_log = push(engine, db, targets, defs, prepared, survey, a.mode)
    engine.dispose()

    pd.DataFrame(summary).to_csv(os.path.join(out, "summary.csv"), index=False)
    isu.to_csv(os.path.join(out, "issues.csv"), index=False)
    pd.DataFrame(all_cols).to_csv(os.path.join(out, "columns.csv"), index=False)
    if push_log:
        pd.DataFrame(push_log).to_csv(os.path.join(out, "push_log.csv"), index=False)
    with open(os.path.join(out, "ids.json"), "w", encoding="utf-8") as f:
        json.dump(ids, f, indent=1, default=str)
    write_report(out, db, folder, survey, ids, a, summary, isu, defs, status, push_log)
    print(f"\n{status}\nReport: {os.path.join(out, 'report.md')}  |  blockers {blockers}, "
          f"warnings {int((isu['severity'] == 'WARNING').sum())}")
    if not a.apply and not blockers:
        print("Nothing was written. Re-run with --apply to push.")
    return 1 if (blockers or status.startswith("FAILED")) else 0


def push(engine, db, targets, defs, prepared, survey, mode):
    """One transaction for every table: deletes (children first), then inserts (parents first).
       After the inserts the survey's rows are counted again in the same transaction."""
    order = [t for t, _, _ in targets if t in prepared]
    log = {t: {"table": t, "deleted": 0, "inserted": 0, "survey_rows_after": None} for t in order}
    raw = engine.raw_connection()
    t0 = time.time()
    try:
        cur = raw.cursor()
        if mode == "replace":
            for table in reversed(order):
                scope = scope_sql(table, [c["name"] for c in defs[table].columns], survey, style="psycopg2")
                if scope is None:
                    continue
                cur.execute(f'DELETE FROM "{db.schema}"."{table}" WHERE {scope[0]}', scope[1])
                log[table]["deleted"] = cur.rowcount
                print(f"  deleted  {table:28s} {cur.rowcount:8,}")
        for table in order:
            cols, data = rows_for_insert(defs[table], prepared[table])
            col_sql = ", ".join(f'"{c}"' for c in cols)
            execute_values(cur, f'INSERT INTO "{db.schema}"."{table}" ({col_sql}) VALUES %s', data,
                           page_size=config.INSERT_PAGE_SIZE)
            log[table]["inserted"] = len(data)
            scope = scope_sql(table, [c["name"] for c in defs[table].columns], survey, style="psycopg2")
            if scope is not None:
                cur.execute(f'SELECT count(*) FROM "{db.schema}"."{table}" WHERE {scope[0]}', scope[1])
                log[table]["survey_rows_after"] = cur.fetchone()[0]
            print(f"  inserted {table:28s} {len(data):8,}")
        raw.commit()
        return f"PUSHED to {db.name} in {time.time() - t0:.0f}s (one transaction, mode {mode})", list(log.values())
    except Exception as e:
        raw.rollback()
        print(f"  ERROR: {type(e).__name__}: {e}")
        return (f"FAILED and ROLLED BACK (database unchanged): {type(e).__name__}: {str(e).splitlines()[0]}",
                list(log.values()))
    finally:
        raw.close()


def write_report(out, db, folder, survey, ids, a, summary, isu, defs, status, push_log):
    L = [f"# DHMS push — {survey['upc']} phase {survey['phase']} cycle {survey['cycle']} → {db.name}", "",
         f"- Run {datetime.now():%Y-%m-%d %H:%M:%S} · {'APPLY' if a.apply else 'DRY RUN'} · mode **{a.mode}** · "
         f"database `{db.name}` schema `{db.schema}` host `{db.host}`",
         f"- Silver `{folder}`", f"- **Result: {status}**", "",
         "BLOCKER = the insert would fail (nothing is pushed while there is one) · WARNING = it would insert, but "
         "not as expected · INFO = for information", "",
         f"## Project ids (from `{db.schema}.{db.master_table}` of `{db.name}`)", "",
         f"{'Found' if ids['found'] else '**Not found**'}: {ids['how']}", "",
         "| Column | Value |", "|---|---|"] + [f"| {k} | {v} |" for k, v in ids["ids"].items()]
    L += ["", "## Tables", "",
          "| Group | Table | In DB | Silver rows | Survey rows in DB | Delete | Insert | Rows found by | Blockers | Warnings |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for s in summary:
        name = s["table"] + (f" ← `{s['silver_file']}`" if s["silver_file"] else "")
        sr = "—" if s["silver_rows"] is None else f"{s['silver_rows']:,}"
        ex = "—" if s["rows_of_survey_in_db"] is None else f"{s['rows_of_survey_in_db']:,}"
        L.append(f"| {s['group']} | {name} | {'yes' if s['exists_in_db'] else '**no**'} | {sr} | {ex} | "
                 f"{s['to_delete']:,} | {s['to_insert']:,} | {s['survey_rows_found_by']} | {s['blockers']} | {s['warnings']} |")
    if push_log:
        L += ["", "## Push", "", "| Table | Deleted | Inserted | Survey rows after |", "|---|---|---|---|"] + \
             [f"| {p['table']} | {p['deleted']:,} | {p['inserted']:,} | {p['survey_rows_after']} |" for p in push_log]
    gen = isu[isu["table"].str.startswith("(")]
    if len(gen):
        L += ["", "## Survey / ids", "", "| Severity | Issue |", "|---|---|"] + \
             [f"| {r['severity']} | {r['issue']} |" for _, r in gen.iterrows()]
    for s in summary:
        t, d = s["table"], defs.get(s["table"])
        sub = isu[isu["table"] == t]
        L += ["", f"## {t}", ""]
        if d is not None and d.exists:
            L.append(f"Primary key: {', '.join(d.primary_key) or 'none'} · unique: "
                     f"{'; '.join(', '.join(u['columns']) for u in d.uniques) or 'none'} · foreign keys: "
                     f"{'; '.join(fk['definition'] for fk in d.foreign_keys) or 'none'} · triggers: "
                     f"{', '.join(x['name'] for x in d.triggers) or 'none'} · definition `ddl/{t}.sql`")
        if len(sub):
            L += ["", "| Severity | Column | Issue | Rows | Examples |", "|---|---|---|---|---|"]
            order = {"BLOCKER": 0, "WARNING": 1, "INFO": 2}
            for _, i in sub.assign(o=sub["severity"].map(order)).sort_values("o").iterrows():
                L.append(f"| {i['severity']} | {i['column']} | {i['issue']} | "
                         f"{'' if pd.isna(i['rows']) else int(i['rows'])} | {'' if pd.isna(i['examples']) else i['examples']} |")
    with open(os.path.join(out, "report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")


if __name__ == "__main__":
    sys.exit(main())
