"""
child_10m_cracking  (silver table list: child_10m, CA + IRC, 10 m)
  One row per lane per 10 m: CA row of master_cracking (D4 10 m cracking) + its IRC twin as irc_* columns, CA values
  copied to ca_*; threshold_name 'As per CA & IRC'; breach_flag = 1 when CA or IRC breaches;
  IDs as DLM: id = NSV master_cracking id with the table name replaced ({UPC}_master_cracking_10m_nnnnnn);
  parent_id = id of the master_cracking_as_per_ca row of its lane and block (D2 CA cracking interval).
Written to <UPC>/dlm_kpi_new (config.HIERARCHY_FOLDER).
Run alone:  python tables/child_10m_cracking.py [--no-excel]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from common.hierarchy import build_child_10m, cracking_ca_interval, table_name, link_children_to_top
from common.output import run_table, standalone

KEY = 'child_10m_cracking'
TABLE = table_name(KEY)          # output name: config.OUTPUT_NAMES


def build(prj):
    out = build_child_10m(prj, 'cracking', TABLE, 'master_cracking', cracking_ca_interval(prj)[0], config.CRACKING_PARENT_BY)
    if out is None or out.empty:
        return out
    # DLM ids: id = NSV master_cracking id with the table name replaced (…_master_cracking_10m_nnnnnn),
    # parent_id = the id of the master_cracking_as_per_ca row of its lane / block
    out['id'] = out['id'].astype(str).str.replace('_master_cracking_', f'_{TABLE}_', regex=False)
    return link_children_to_top(prj, out, 'master_cracking_ca', table_name('master_cracking_ca'))


def run(prj, write=True):
    return run_table(TABLE, build, prj, write, apply_finalize=False, folder=config.HIERARCHY_FOLDER, key=KEY)


if __name__ == '__main__':
    standalone(run)
