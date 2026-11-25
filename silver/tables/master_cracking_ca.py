"""
master_cracking_ca  → output master_cracking_as_per_ca (DLM table list: lane, CA, top level, D2 CA interval)
  Lane-wise, like child_roughness_as_per_ca / child_rutting_as_per_ca: one row per lane per block
  (config.CRACKING_PARENT_BY = 'lane'; 'side' → one row per LHS / RHS instead), block = the D2 CA cracking
  reporting interval — same rule as child_roughness_as_per_ca / child_rutting_as_per_ca:
     D2 'Comparison as per CA' 'Current Cycle | Cracking … | Reporting Interval', else 'Overall Reporting Interval',
     else config.CHILD_AS_PER_CA_DEFAULT_M (IRC sheet never used). config.CRACKING_PARENT_FROM_D2 = False → fixed
     config.CRACKING_PARENT_M (old behaviour, 100 m).
  Source = CA rows of the D4 10 m cracking aggregated DIRECTLY from 10 m to that interval, the same way 10 m → 100 m
  is done (100 m → master_cracking_child_100m; any other interval, 1000 m included → the 100 m build re-run at the
  interval, i.e. average of the 10 m rows of the block; master_cracking_parent is NOT used). Lane values are checked
  against the D2 CA 'Cracking Lane Area' values (log + dq/master_cracking_as_per_ca_vs_d2.csv).
  Lane-wise: cracking % / area / breach = the lane's own CA values at the interval (CA threshold from D2).
  ('side' mode: % = average of the side's lane values, area = total, breach = any lane breaches.)
  Image names: NSV logic.
  IDs as DLM (top table without a parent): id = the source row's id with the table name replaced
  ({UPC}_master_cracking_as_per_ca_{nnnnnn}), record_id / source_defect_id = the source row's (cracking_parent_nnnnnn
  at 1000 m, cracking_child_100m_nnnnnn otherwise), parent_id NULL; master_cracking_10m rows carry this id as
  parent_id.
Written to <UPC>/dlm_kpi_new (config.HIERARCHY_FOLDER).
Run alone:  python tables/master_cracking_ca.py [--no-excel]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from common.hierarchy import (build_side_parent, check_vs_d2, cracking_ca_interval, _ca_rows, _rows_at_interval,
                              table_name, dlm_top_ids)
from common.output import built_table, run_table, standalone

KEY = 'master_cracking_ca'
TABLE = table_name(KEY)          # output name: config.OUTPUT_NAMES


def build(prj):
    iv, how = cracking_ca_interval(prj)
    if iv == int(config.CHILD_100M):                   # 10 m → 100 m (already built)
        src_t = 'master_cracking_child_100m'
        src = built_table(prj, src_t)
    else:                                              # 10 m → iv (500 m, 1000 m, …) with the same 100 m logic
        src_t = f'master_cracking_child_{iv}m'
        src = _rows_at_interval(prj, 'cracking', iv)
    print(f"  CA reporting interval {iv} m ({how}) → CA rows of {src_t}")
    if src is not None and not src.empty:
        check_vs_d2(prj, 'cracking', _ca_rows(src), iv, TABLE)
    out = build_side_parent(prj, 'cracking', TABLE, src_t, iv, config.CRACKING_PARENT_BY, src=src)
    return dlm_top_ids(prj, out, src, src_t, TABLE, iv)            # DLM ids: source row id, parent_id NULL


def run(prj, write=True):
    return run_table(TABLE, build, prj, write, apply_finalize=False, folder=config.HIERARCHY_FOLDER, key=KEY)


if __name__ == '__main__':
    standalone(run)
