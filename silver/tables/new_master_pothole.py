"""
master_pothole  (DLM table, key new_master_pothole) — one row per pothole (defect records)
  Every NSV pothole (master_pothole in dlm_kpi, D4 NHAI One sheet) as one row in the DLM pothole column list:
  values, image names and the severity breach rule unchanged; side + LHS / RHS 1 km block added.
  The NSV master_pothole in dlm_kpi is not changed. No child table.
Written to <UPC>/dlm_kpi_new (config.HIERARCHY_FOLDER).
Run alone:  python tables/new_master_pothole.py [--no-excel]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from common.hierarchy import build_pothole_records, table_name
from common.output import run_table, standalone

KEY = 'new_master_pothole'
TABLE = table_name(KEY)          # output name: config.OUTPUT_NAMES


def build(prj):
    return build_pothole_records(prj)


def run(prj, write=True):
    return run_table(TABLE, build, prj, write, apply_finalize=False, folder=config.HIERARCHY_FOLDER, key=KEY)


if __name__ == '__main__':
    standalone(run)
