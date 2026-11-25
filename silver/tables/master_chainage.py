"""
master_chainage
  One row per 10 m chainage of the UPC (column layout as the DLM notebook's master_chainage).
  Sources: deliverables only — D4 defect report (lanes, chainage, lat/long), D1 (NH, section, carriageway,
  pavement type, pavement extent), folder path (upc, zone, phase, cycle). No backup source is used.

  chainage_start / chainage_end   straight from the D4 defect report rows (no D1): one row per D4 10 m,
                                  chainage_end = chainage_start + 10
  upc_start_chainage / _end       smallest chainage_start / biggest chainage_end of the D4 rows (same on every row)
  <lane>_start_chainage … _end_long
                                  D4 row of that lane whose start falls in the 10 m (floor to 10 m, as DLM):
                                  min start, max end, first non-blank lat/long. config.MASTER_CHAINAGE_LANES
                                  (L1, L2, R1, R2) are always written (NULL when not in D4); any other D4 lane with
                                  data (L3, L4, R3 …) gets its 6 columns added too, in lane order
  lanes_present                   D4 lanes that have DATA in the 10 m (at least one value / condition / lat-long in
                                  that lane's cells — a lane in the header with blank cells is not counted)
  lhs_* / rhs_*                   L lanes / R lanes of the 10 m: start chainage = min, end chainage = max,
                                  lanes = list, lat/long per config.MASTER_CHAINAGE_SIDE_GPS
                                  ('lane_extent' = DLM: start lat/long of the lane with the lowest start,
                                  end lat/long of the lane with the highest end; tie → lowest lane number)
  lhs_pavement_type / rhs_pavement_type
                                  D1 pavement type for the side's direction (Increasing / Decreasing); filled where
                                  the side has D4 lanes in the 10 m or the 10 m lies inside a D1 pavement interval
                                  of that direction, else NULL
  nh_number / section_code / carriageway
                                  D1 lookup at chainage_start, Increasing side (unchanged)
  created_at                      run timestamp
  Every column is always written, also when it is NULL for the whole UPC.
Run alone:  python tables/master_chainage.py [--no-excel]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

import config
from common.output import run_table, standalone

TABLE = 'master_chainage'
STEP = int(config.CHILD_10M)
GPS = ['start_lat', 'start_long', 'end_lat', 'end_long']
LANE_FIELDS = ['start_chainage', 'end_chainage'] + GPS
SIDES = [('lhs', 'L', 'Increasing', 'L1'), ('rhs', 'R', 'Decreasing', 'R1')]

def _columns(lanes):
    return (
        ['upc', 'upc_code', 'zone', 'phase', 'cycle', 'chainage_start', 'chainage_end',
         'upc_start_chainage', 'upc_end_chainage', 'lanes_present', 'nh_number', 'section_code', 'carriageway']
        + [f'{s}_{c}' for s, *_ in SIDES for c in
           ['start_chainage', 'start_lat', 'start_long', 'pavement_type',
            'end_chainage', 'end_lat', 'end_long', 'lanes']]
        + [f'{l.lower()}_{c}' for l in lanes for c in LANE_FIELDS]
        + ['created_at'])


COLUMNS = _columns(config.MASTER_CHAINAGE_LANES)        # the fixed 54 columns
TEXT_COLS = {'upc', 'upc_code', 'zone', 'phase', 'lanes_present', 'nh_number', 'section_code', 'carriageway',
             'lhs_pavement_type', 'rhs_pavement_type', 'lhs_lanes', 'rhs_lanes'}


def _lane_key(lane):
    return lane[0], int(lane[1:]) if lane[1:].isdigit() else 99


_KEYS = {'lane', 'start_chainage', 'end_chainage', 'nh_number', 'row_id', 'source_row'}


def lane_has_data(d4):
    """D4 lane-row really surveyed: at least one value (any defect value, visual condition or lat / long) in that
    lane's columns of that row. A lane column in the D4 header with blank cells does not count."""
    vals = [c for c in d4.columns if c not in _KEYS]
    return d4[vals].notna().any(axis=1) if vals else pd.Series(True, index=d4.index)


def starts_of(d):
    return (np.floor(d['start_chainage'].astype(float) / STEP) * STEP).astype('int64').values


def _lane_rows(d4):
    """One row per lane per 10 m: s = min start, e = max end, first non-blank lat/long (DLM logic).
    Only lane-rows with data (lane_has_data) — so lanes_present / lhs_lanes / rhs_lanes never list a lane whose
    D4 cells are blank in that 10 m."""
    x = d4.loc[lane_has_data(d4), ['lane', 'start_chainage', 'end_chainage'] + GPS].copy()
    x['lane'] = x['lane'].astype(str).str.strip().str.upper()
    x = x.dropna(subset=['start_chainage'])
    x['chainage_start'] = (np.floor(x['start_chainage'].astype(float) / STEP) * STEP).astype('int64')
    x = x.sort_values(['lane', 'start_chainage'], kind='mergesort')
    lb = (x.groupby(['lane', 'chainage_start'], sort=False, as_index=False)
          .agg(s=('start_chainage', 'min'), e=('end_chainage', 'max'), **{c: (c, 'first') for c in GPS}))
    lb['lane_order'] = lb['lane'].map(_lane_key)
    return lb.sort_values(['chainage_start', 'lane_order'], kind='mergesort').reset_index(drop=True)


def _side(lb, prefix, side, mode):
    """LHS (L lanes) or RHS (R lanes) per 10 m."""
    cols = [f'{side}_{c}' for c in ['start_chainage', 'end_chainage', 'lanes'] + GPS]
    g = lb[lb['lane'].str.startswith(prefix)].reset_index(drop=True)
    if g.empty:
        return pd.DataFrame(columns=['chainage_start'] + cols)
    grp = g.groupby('chainage_start', sort=True)
    out = grp.agg(**{f'{side}_start_chainage': ('s', 'min'), f'{side}_end_chainage': ('e', 'max'),
                     f'{side}_lanes': ('lane', lambda v: ','.join(v))}).reset_index()   # g is in lane order
    if mode == 'lane_extent':
        # DLM: start lat/long of the lane with the lowest start, end lat/long of the lane with the highest end
        st = g.loc[grp['s'].idxmin().values, ['chainage_start', 'start_lat', 'start_long']]
        en = g.loc[grp['e'].idxmax().values, ['chainage_start', 'end_lat', 'end_long']]
        gps = st.merge(en, on='chainage_start', how='outer')
    else:
        # meeting rule: each coordinate on its own — min of start lat/long, max of end lat/long
        gps = grp.agg(start_lat=('start_lat', 'min'), start_long=('start_long', 'min'),
                      end_lat=('end_lat', 'max'), end_long=('end_long', 'max')).reset_index()
    gps = gps.rename(columns={c: f'{side}_{c}' for c in GPS})
    return out.merge(gps, on='chainage_start', how='left')


def _inside_pavement(pav, ch, direction):
    """True where chainage ch lies inside a D1 pavement interval of exactly this direction
    (D1 without a direction column → any interval)."""
    if pav.empty:
        return np.zeros(len(ch), bool)
    d = pav['direction'].fillna('').str.lower() if 'direction' in pav else pd.Series('', index=pav.index)
    pv = pav[d == direction.lower()] if (d != '').any() else pav
    if pv.empty:
        return np.zeros(len(ch), bool)
    S, E = pv['start'].values.astype(float), pv['end'].values.astype(float)
    c = np.asarray(ch, float)[:, None]
    return ((S <= c) & (c < E)).any(axis=1)


def build(prj):
    d4 = prj.d4_long

    # ── chainage spine: the D4 defect report rows only (no D1) — one row per D4 10 m (start floored to 10 m) ──
    d4c = d4.dropna(subset=['start_chainage'])
    starts = sorted({int(c) for c in (np.floor(d4c['start_chainage'].astype(float) / STEP) * STEP)})
    out = pd.DataFrame({'chainage_start': pd.Series(starts, dtype='int64')})
    out['chainage_end'] = out['chainage_start'] + STEP

    # ── per-lane columns from D4 ──
    lb = _lane_rows(d4)
    d4_lanes = sorted(lb['lane'].unique(), key=_lane_key)
    lanes = sorted(set(config.MASTER_CHAINAGE_LANES) | set(d4_lanes), key=_lane_key)
    extra = [l for l in lanes if l not in config.MASTER_CHAINAGE_LANES]
    if extra:
        print(f"  D4 lanes {extra} have data → their lane columns are added")
    columns = _columns(lanes)
    for lane in lanes:
        p = lane.lower()
        x = lb.loc[lb['lane'] == lane, ['chainage_start', 's', 'e'] + GPS].rename(columns={
            's': f'{p}_start_chainage', 'e': f'{p}_end_chainage', **{c: f'{p}_{c}' for c in GPS}})
        out = out.merge(x, on='chainage_start', how='left')

    present = lb.groupby('chainage_start')['lane'].agg(','.join).rename('lanes_present').reset_index()
    out = out.merge(present, on='chainage_start', how='left')

    # chainage_end = start + 10 (end_chainage of the D4 rows is start + 10 on every row)
    d4_end = (d4c.assign(chainage_start=starts_of(d4c)).groupby('chainage_start')['end_chainage'].max()
              .rename('_d4_end').reset_index())
    out = out.merge(d4_end, on='chainage_start', how='left')
    out['chainage_end'] = out['_d4_end'].where(out['_d4_end'].notna(), out['chainage_start'] + STEP)
    out = out.drop(columns='_d4_end')

    # ── LHS / RHS ──
    mode = getattr(config, 'MASTER_CHAINAGE_SIDE_GPS', 'lane_extent')
    pav = prj.road.pavement                                   # D1 pavement type (lhs / rhs_pavement_type only)
    for side, prefix, direction, rep_lane in SIDES:
        out = out.merge(_side(lb, prefix, side, mode), on='chainage_start', how='left')
        ch = out['chainage_start'].values.astype(float)
        pt = prj.road.lookup(ch, [rep_lane] * len(out))['pavement_type'].values
        keep = out[f'{side}_lanes'].notna().values | _inside_pavement(pav, ch, direction)
        out[f'{side}_pavement_type'] = np.where(keep, pt, None)

    # ── D1 road context (unchanged: Increasing-side lookup) ──
    ctx = prj.road.lookup(out['chainage_start'].values.astype(float), ['L1'] * len(out))
    out['nh_number'] = ctx['nh_number'].values
    out['section_code'] = ctx['section_code'].values
    out['carriageway'] = ctx['carriageway'].values

    # ── project columns ──
    out['upc'] = prj.upc_slash
    out['upc_code'] = prj.upc
    out['zone'] = prj.zone
    out['phase'] = prj.phase
    out['cycle'] = int(prj.cycle)
    out['upc_start_chainage'] = float(out['chainage_start'].min()) if len(out) else np.nan
    out['upc_end_chainage'] = float(out['chainage_end'].max()) if len(out) else np.nan
    out['created_at'] = prj.now_ts

    # ── every column, fixed order and types (all-NULL columns are kept) ──
    for c in columns:
        if c not in out:
            out[c] = None
    out = out[columns].copy()
    for c in columns:
        if c in TEXT_COLS:
            out[c] = out[c].astype(object).where(out[c].notna(), None)
        elif c in ('chainage_start', 'chainage_end'):
            v = pd.to_numeric(out[c], errors='coerce').astype(float)
            out[c] = v.astype('int64') if v.notna().all() and (v % 1 == 0).all() else v   # whole metres → integer
        elif c == 'cycle':
            out[c] = out[c].astype(int)
        elif c == 'created_at':
            out[c] = pd.to_datetime(out[c])
        else:
            out[c] = pd.to_numeric(out[c], errors='coerce').astype(float)
    return out.sort_values('chainage_start').reset_index(drop=True)


def run(prj, write=True):
    return run_table(TABLE, build, prj, write, apply_finalize=False, breach_copy=False,
                     folder=config.HIERARCHY_FOLDER)


if __name__ == '__main__':
    standalone(run)
