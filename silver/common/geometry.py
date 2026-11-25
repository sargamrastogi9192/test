"""
geometry_parent / geometry_child — same logic as the DLM pipeline (src/hierarchy/geometry_builder.py), built from
this run's master_chainage (D4 lanes / chainage / lat-long, D1 LRMS NH + section).

geometry_child (13 columns): one row per lane per 10 m of master_chainage where that lane has D4 data
  (master_chainage lane columns are filled only from D4 lane-rows with data; L1 / L2 / R1 / R2 columns that are
  always written but NULL do not give rows).
  id = child_id = {UPC}_{lane}_{int(start)}_{int(end)}; parent_id = {UPC}_geometry_parent
  lane, start / end chainage, start / end latitude / longitude  ← the lane's D4 values in master_chainage
  nh_number / section_code  ← ONE value for the whole UPC (DLM): first non-null of master_chainage (D1 LRMS),
                               NH as NH-<n>; none → 'NH-Unknown' / ''
  direction  ← config.LANE_TO_DIRECTION (L = Increasing, R = Decreasing)
  Rows in lane order (L1, L2 … R1 …), then chainage.

geometry_parent (22 columns, one row):
  id {UPC}_geometry_parent; layer_id 'dlm'; upc = project_id = UPC; source_system NSV; defect_type 'All'
  lanes            lanes that have child rows, sorted, comma-separated
  start / end_chainage   smallest start / biggest end of the child rows (D4)
  start_lat / long first non-null child start lat / long (first lane, first row);
  end_lat / long   last non-null child end lat / long (last lane, last row)            ← DLM
  total_records    number of child rows
  lane_length_surveyed_km = accepted_lane_length_km = Σ |end − start| of the child rows / 1000 (3 dp)
  survey_start / end_date  tracker 'Actual Survey Start / End Date'
  d2_status 'Accepted', project_details '{}'                                          ← DLM (hardcoded)
"""
import numpy as np
import pandas as pd

import config
from common.output import built_table
from common.utils import standardize_nh, lane_dir

PARENT_COLUMNS = ['id', 'layer_id', 'upc', 'project_id', 'nh_number', 'section_code', 'source_system', 'defect_type',
                  'lanes', 'start_chainage', 'end_chainage', 'start_lat', 'start_long', 'end_lat', 'end_long',
                  'total_records', 'survey_start_date', 'survey_end_date', 'lane_length_surveyed_km',
                  'accepted_lane_length_km', 'd2_status', 'project_details']
CHILD_COLUMNS = ['id', 'child_id', 'parent_id', 'lane', 'nh_number', 'section_code', 'direction',
                 'start_chainage', 'end_chainage', 'start_latitude', 'start_longitude', 'end_latitude',
                 'end_longitude']


def _lane_key(lane):
    return lane[0], int(lane[1:]) if lane[1:].isdigit() else 99


def _first(mc, col):
    v = mc[col].dropna() if col in mc else pd.Series(dtype=object)
    v = v[v.astype(str).str.strip() != '']
    return v.iloc[0] if len(v) else None


def _num(v):
    return float(v) if v is not None and pd.notna(v) else None


def build_geometry(prj):
    """(geometry_parent, geometry_child) — built once per run, cached on prj."""
    cached = prj.__dict__.get('_geometry')
    if cached is not None:
        return cached
    mc = built_table(prj, 'master_chainage')
    parent_id = f"{prj.upc}_geometry_parent"
    mc = mc if mc is not None else pd.DataFrame()

    nh = standardize_nh(_first(mc, 'nh_number')) or 'NH-Unknown'
    sec = _first(mc, 'section_code') or ''

    lanes = sorted({c[:-len('_start_chainage')].upper() for c in mc.columns
                    if c.endswith('_start_chainage') and not c.startswith(('lhs_', 'rhs_', 'upc_'))}, key=_lane_key)
    parts = []
    for lane in lanes:
        p = lane.lower()
        x = mc[mc[f'{p}_start_chainage'].notna()]
        if x.empty:                                   # lane column written but no D4 data → no rows
            continue
        s = x[f'{p}_start_chainage'].astype(float)
        e = x[f'{p}_end_chainage'].astype(float).where(x[f'{p}_end_chainage'].notna(), s + 10.0)
        parts.append(pd.DataFrame({
            'lane': lane, 'start_chainage': s.values, 'end_chainage': e.values,
            'start_latitude': x[f'{p}_start_lat'].astype(float).values,
            'start_longitude': x[f'{p}_start_long'].astype(float).values,
            'end_latitude': x[f'{p}_end_lat'].astype(float).values,
            'end_longitude': x[f'{p}_end_long'].astype(float).values}))
    child = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=CHILD_COLUMNS)
    if len(child):
        child['id'] = [f"{prj.upc}_{l}_{int(s)}_{int(e)}"
                       for l, s, e in zip(child['lane'], child['start_chainage'], child['end_chainage'])]
        child['child_id'] = child['id']
        child['parent_id'] = parent_id
        child['nh_number'] = nh
        child['section_code'] = sec
        child['direction'] = [lane_dir(l) or ('Increasing' if l.startswith('L') else 'Decreasing')
                              for l in child['lane']]
    child = child.reindex(columns=CHILD_COLUMNS)

    def first_valid(col, last=False):
        v = child[col].dropna()
        return _num(v.iloc[-1 if last else 0]) if len(v) else None

    lane_km = round(float((child['end_chainage'] - child['start_chainage']).abs().sum()) / 1000.0, 3) \
        if len(child) else 0.0
    trk = prj.tracker_ctx
    parent = pd.DataFrame([{
        'id': parent_id, 'layer_id': 'dlm', 'upc': prj.upc, 'project_id': prj.upc,
        'nh_number': nh, 'section_code': sec, 'source_system': config.SOURCE_SYSTEM, 'defect_type': 'All',
        'lanes': ','.join(sorted(child['lane'].unique())) if len(child) else '',
        'start_chainage': _num(child['start_chainage'].min()) if len(child) else None,
        'end_chainage': _num(child['end_chainage'].max()) if len(child) else None,
        'start_lat': first_valid('start_latitude'), 'start_long': first_valid('start_longitude'),
        'end_lat': first_valid('end_latitude', last=True), 'end_long': first_valid('end_longitude', last=True),
        'total_records': int(len(child)),
        'survey_start_date': trk.get('survey_start_date'), 'survey_end_date': trk.get('survey_end_date'),
        'lane_length_surveyed_km': lane_km, 'accepted_lane_length_km': lane_km,
        'd2_status': 'Accepted', 'project_details': '{}',
    }], columns=PARENT_COLUMNS)
    parent['total_records'] = parent['total_records'].astype('int64')
    print(f"  geometry: {len(child):,} child rows, lanes {parent['lanes'].iloc[0] or '-'}, {lane_km} lane-km")
    prj.__dict__['_geometry'] = (parent, child)
    return parent, child
