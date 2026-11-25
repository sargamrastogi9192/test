"""
Output and running:
  • write_table   → parquet / Excel / CSV into dlm_kpi (all rows) and dlm_postgres (breach rows)
  • run_table     → build → finalize → write, with a one-line summary
  • parse_args / make_project / standalone → command-line options shared by main.py and table scripts
"""
import argparse
import os
import time

import pandas as pd

import config


# ═════════════════════════════════════════════════════════════════════════════
# Writer
# ═════════════════════════════════════════════════════════════════════════════
EXCEL_MAX_ROWS = 1_048_575


def _excel_ready(df):
    x = df.copy()
    for c in x.columns:
        # only text/object columns can hold lists; on an empty table a datetime column would fail .any()
        if x[c].dtype == object and x[c].map(lambda v: isinstance(v, list)).astype(bool).any():
            x[c] = x[c].map(lambda v: ', '.join(map(str, v)) if isinstance(v, list) else v)
    return x


def _write_one(df, folder, table):
    os.makedirs(folder, exist_ok=True)
    base = os.path.join(folder, table)
    if config.WRITE_PARQUET:
        df.to_parquet(base + '.parquet', index=False)
    if config.WRITE_CSV:
        df.to_csv(base + '.csv', index=False, encoding='utf-8-sig')
    if config.WRITE_EXCEL:
        if len(df) > EXCEL_MAX_ROWS:
            print(f"    skip Excel for {table}: {len(df):,} rows > Excel limit")
            return
        try:
            import xlsxwriter  # noqa: F401
            engine = 'xlsxwriter'
        except ImportError:
            engine = 'openpyxl'
        sheet = table[:31]
        target = base + '.xlsx'
        try:
            _write_excel(df, target, engine, sheet)
        except PermissionError:
            # file is open in Excel (Windows locks it) → write a copy next to it, do not fail the table
            alt = f"{base}_{pd.Timestamp.now():%Y%m%d_%H%M%S}.xlsx"
            print(f"    WARNING: {os.path.basename(target)} is open in Excel (locked) → written as {os.path.basename(alt)}")
            _write_excel(df, alt, engine, sheet)


def _write_excel(df, path, engine, sheet):
    with pd.ExcelWriter(path, engine=engine) as w:
        _excel_ready(df).to_excel(w, sheet_name=sheet, index=False)
        if engine == 'xlsxwriter':
            ws = w.sheets[sheet]
            ws.freeze_panes(1, 2)
            ws.autofilter(0, 0, max(len(df), 1), len(df.columns) - 1)


def write_table(df, table, prj, breach_copy=True, folder='dlm_kpi'):
    """Returns (rows_kpi, rows_postgres). folder 'dlm_kpi' (default) also writes the breach rows to dlm_postgres;
    any other folder (e.g. 'dlm_kpi_new' for the DLM LHS / RHS hierarchy) gets all rows only."""
    kpi = os.path.join(prj.out_dir, folder)
    _write_one(df, kpi, table)
    n_pg = None
    if folder == 'dlm_kpi' and breach_copy and 'breach_flag' in df.columns:
        pg = df[df['breach_flag'] == 1]
        _write_one(pg, os.path.join(prj.out_dir, 'dlm_postgres'), table)
        n_pg = len(pg)
    return len(df), n_pg


# ═════════════════════════════════════════════════════════════════════════════
# Runner
# ═════════════════════════════════════════════════════════════════════════════
def summarize(table, df):
    if df.empty:
        return f"{table}: 0 rows"
    msg = f"{table}: {len(df):,} rows"
    if 'threshold_name' in df.columns and df['threshold_name'].nunique() > 1:
        ca, irc = df[df['threshold_name'] == 'As per CA'], df[df['threshold_name'] == 'As per IRC']
        msg += f" ({len(ca):,} CA + {len(irc):,} IRC) | breach CA {int(ca['breach_flag'].sum()):,}, IRC {int(irc['breach_flag'].sum()):,}"
    elif 'breach_flag' in df.columns:
        msg += f" | breach {int(df['breach_flag'].sum()):,}"
    return msg


def run_table(table, build_fn, prj, write=True, apply_finalize=True, breach_copy=True, folder='dlm_kpi', key=None):
    """key = name this table is known by inside the run (default: table); needed when two tables share a name
    in different folders (e.g. NSV master_pothole in dlm_kpi and the DLM LHS / RHS master_pothole in dlm_kpi_new)."""
    t0 = time.time()
    print(f"\n▶ {table}" + (f"  ({folder})" if folder != 'dlm_kpi' else ''))
    from common.finalize import finalize
    df = build_fn(prj)
    if apply_finalize:
        df = finalize(df, table, prj)
    if folder == getattr(config, 'HIERARCHY_FOLDER', None):
        df = fill_master_project(df, prj)                            # project_id, region_id … from the master
    prj.__dict__.setdefault('_built_tables', {})[key or table] = df       # reused by tables built from this one
    print(f"  {summarize(table, df)}")
    if write and not df.empty:
        write_table(df, table, prj, breach_copy=breach_copy, folder=folder)
        print(f"  written ({time.time() - t0:.0f}s)")
    elif df.empty:
        print("  nothing to write")
    return df


def fill_master_project(df, prj):
    """project_id / region_id / piu_id / state_id / zone_id / contractor_id / ae_ie_id from the project master
       (config.MASTER_PROJECT_COLUMNS) — only into columns the table has; a value already set is kept."""
    if df is None or df.empty:
        return df
    try:
        master = prj.master_project
    except Exception as e:                       # the master must never stop a table
        print(f"  WARNING: project master not applied ({type(e).__name__}: {e})")
        return df
    vals = {c: v for c, v in (master or {}).items() if c in df.columns and v is not None}
    if not vals:
        return df
    df = df.copy()
    for c, v in vals.items():
        df[c] = df[c].where(df[c].notna() & (df[c].astype(str).str.strip() != ''), v)
    return df


def built_table(prj, key):
    """Finished DataFrame of another table of this run, by its key (= module name in tables/);
    built now, without writing, if not built yet."""
    cache = prj.__dict__.setdefault('_built_tables', {})
    if key not in cache:
        import importlib
        importlib.import_module(f'tables.{key}').run(prj, write=False)
    return cache[key]


def standalone(run_fn):
    """Entry point for `python tables/<table>.py [options]`."""
    args = parse_args()
    prj = make_project(args)
    prj.describe()
    prj.print_thresholds()
    run_fn(prj)


# ═════════════════════════════════════════════════════════════════════════════
# Command line
# ═════════════════════════════════════════════════════════════════════════════
def parse_args(extra=None):
    ap = argparse.ArgumentParser(description="NSV silver layer (new structure)")
    ap.add_argument('--project', help='UPC folder that contains d1..d4 (default: config.PROJECT_DIR)')
    ap.add_argument('--tracker', help='NSV Activity Tracker .xlsx (default: config.TRACKER_PATH)')
    ap.add_argument('--out', help='Output root (default: config.OUTPUT_ROOT)')
    ap.add_argument('--schema', help='Schema workbook .xlsx (optional)')
    ap.add_argument('--master-project', help='Project master master_upc_project.csv (default: config.MASTER_PROJECT_PATH)')
    ap.add_argument('--no-excel', action='store_true', help='Do not write .xlsx (faster)')
    ap.add_argument('--csv', action='store_true', help='Also write .csv')
    if extra:
        extra(ap)
    args = ap.parse_args()
    if args.master_project:
        config.MASTER_PROJECT_PATH = args.master_project
    if args.no_excel:
        config.WRITE_EXCEL = False
    if args.csv:
        config.WRITE_CSV = True
    return args


def make_project(args):
    from common.project import Project
    return Project(project_dir=args.project, tracker_path=args.tracker,
                   output_root=args.out, schema_workbook=args.schema)
