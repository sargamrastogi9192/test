"""
master_roughness  (DLM table name) — CA only
  One row per side (LHS = all L lanes, RHS = all R lanes) per 1 km. Every 10 m CA row of the side's lanes in the
  km is pooled: side IRI = average of the 10 m IRI, side BI = 630 × IRI^1.12; breach = 1 when any lane's CA row (master_roughness_as_per_ca) in the km
  breaches; rating_ca / severity calculated from the side value (CA only).
  id = {UPC}_master_roughness_{LHS|RHS}_{km}.
  parent_id = record_id = {UPC}_{LHS|RHS}_{km start}_{km end} (children link to it).
Written to <UPC>/dlm_kpi_new (config.HIERARCHY_FOLDER).
Run alone:  python tables/master_roughness.py [--no-excel]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from common.hierarchy import build_side_parent, table_name
from common.output import run_table, standalone

KEY = 'master_roughness'
TABLE = table_name(KEY)          # output name: config.OUTPUT_NAMES


def build(prj):
    return build_side_parent(prj, 'roughness')


def run(prj, write=True):
    return run_table(TABLE, build, prj, write, apply_finalize=False, folder=config.HIERARCHY_FOLDER, key=KEY)


if __name__ == '__main__':
    standalone(run)
