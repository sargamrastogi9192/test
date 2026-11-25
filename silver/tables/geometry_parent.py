"""
geometry_parent  (DLM table list; same logic as the DLM pipeline's geometry_builder — see common/geometry.py).
Built from this run's master_chainage (D4 lanes / chainage / lat-long with data, D1 LRMS NH + section).
Written to <UPC>/dlm_kpi_new (config.HIERARCHY_FOLDER).
Run alone:  python tables/geometry_parent.py [--no-excel]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from common.geometry import build_geometry
from common.output import run_table, standalone

TABLE = 'geometry_parent'


def build(prj):
    return build_geometry(prj)[0]


def run(prj, write=True):
    return run_table(TABLE, build, prj, write, apply_finalize=False, breach_copy=False,
                     folder=config.HIERARCHY_FOLDER)


if __name__ == '__main__':
    standalone(run)
