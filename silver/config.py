"""
Project settings. Edit the paths here, or override them on the command line:
    python main.py --project <dir> --tracker <xlsx> --out <dir>
"""

# ── Input / output paths ─────────────────────────────────────────────────────
PROJECT_DIR     = r"D:\NSV\New_Structure_Data\zone=B\zone=B\phase=3\cycle=1\N-02017-03001-RJ"
# NSV Activity Tracker: NOT used for new-format surveys — survey dates come from D1 / D4 and the CA / IRC standard
# from D2 (Project.survey_date_range, Project.data_standards). Only an explicit --tracker reads one.
import glob as _glob
import os as _os
_TRACKERS = sorted(_glob.glob(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'NSV_Activity_Tracker*.xls*')),
                   key=_os.path.getmtime)
TRACKER_PATH    = None
OUTPUT_ROOT     = r"D:\NSV\New_Structure_Data\New_Structure_Output"

# Project master (DIC staging export, one row per UPC with the platform UUIDs): the newest 'master_upc_project*.csv'
# in this code folder, else env NSV_MASTER_PROJECT, else none. --master-project overrides it.
#   S3: s3://dhms-new-stgae/NSV/DHMS/DIC_Staging/Latest/master_upc_project.csv (run_silver_s3.sh downloads it)
_MASTERS = sorted(_glob.glob(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'master_upc_project*.csv')),
                  key=_os.path.getmtime)
MASTER_PROJECT_PATH = _MASTERS[-1] if _MASTERS else _os.environ.get('NSV_MASTER_PROJECT', '')
# output column (dlm_kpi_new tables) ← master_upc_project column. Row = UPC equal to 'upc' (else one of
# 'alternate_upcs'). UPC not in the master → these columns stay NULL (warning in the log).
MASTER_PROJECT_COLUMNS = {
    'project_id': 'id', 'region_id': 'region_id', 'piu_id': 'piu_id', 'state_id': 'state_id',
    'zone_id': 'nsv_zone_id', 'contractor_id': 'contractor_id', 'ae_ie_id': 'ae_ie_id',
}
SCHEMA_WORKBOOK = None   # r"D:\NSV\Schema\nsv_defect_schema_master 1.xlsx"  (optional)

# ── Output formats ───────────────────────────────────────────────────────────
WRITE_PARQUET = True
WRITE_EXCEL   = False    # ~1.5 min per 90k-row table; use --no-excel while testing
WRITE_CSV     = True

# ── Segment sizes ────────────────────────────────────────────────────────────
PARENT_LENGTH_M = 1000   # 1 km parent table
CHILD_100M      = 100
CHILD_10M       = 10
DQ_ROUND_DECIMALS = 2

# parent_id / parent_start_chainage / parent_end_chainage block per level — as the DLM notebook:
#   every level (10 m incl. cracking and pothole, 100 m, 1 km) → its 1 km block: {UPC}_{lane}_{km start}_{km end}
#   (display_defect_id links a 10 m / 100 m row to its 1 km parent through this parent_id)
PARENT_BLOCK_BY_LEVEL = {'child_10m': 1000, 'child_100m': 1000, 'parent': 1000}

# display_defect_id (DLM notebook post-process): output tables whose rows get a top-level id, and child → parent
DISPLAY_ID_BASE_TABLES = ['master_roughness_parent', 'master_rutting_parent', 'master_cracking_parent',
                          'master_pothole']
DISPLAY_ID_CHILD_PARENT = {
    'master_cracking_child_100m': 'master_cracking_parent',
    'master_cracking': 'master_cracking_parent',
    'master_roughness_child_100m': 'master_roughness_parent',
    'master_roughness_child_10m': 'master_roughness_parent',
    'master_rutting_child_100m': 'master_rutting_parent',
    'master_rutting_child_10m': 'master_rutting_parent',
}

# ── Fixed values ─────────────────────────────────────────────────────────────
SOURCE_SYSTEM = "NSV"
LANE_AREA_SQM = 35.0     # 10 m × 3.5 m lane, used for ravelling % and cracking JSON

# Good / Fair / Poor ranges per defect → defect_rules.json (thresholds come ONLY from D2, no defaults)
import json as _json
import os as _os
DEFECT_RULES_PATH = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'defect_rules.json')
with open(DEFECT_RULES_PATH, encoding='utf-8') as _f:
    DEFECT_RULES = _json.load(_f)
# Defects that take a threshold — ONLY from D2 (no defaults anywhere). No D2 threshold → threshold_value,
# breach_flag and road_condition NULL, calculated severity / ratings NULL, and the DQ 'threshold' check FAILs.
THRESHOLD_DEFECTS = ['roughness', 'rutting', 'cracking', 'ravelling']

# ── IRC only (CA is unchanged: D2 only, no defaults) ─────────────────────────
#   Standard IRC breach thresholds (D-3 SOP colour code; the Poor threshold = breach). Used when D2 gives NO IRC
#   threshold for the defect — a D2 IRC threshold always has priority. Same value for Bituminous (D1 'Asphalt')
#   and Cement Concrete (cracking 30 % is applied to CC as well). Breach = value > threshold (unless D2 gives the
#   operator for its own IRC threshold).
IRC_STANDARD_THRESHOLDS = {'roughness': 2400.0,      # mm/km (BI)
                           'rutting': 10.0,          # mm
                           'cracking': 30.0}         # % lane area
IRC_DEFAULT_OPERATOR = '>'
PAVEMENT_ALIASES = {'asphalt': 'Bituminous'}          # D1 'Asphalt' = Bituminous

# PCI weights (sum = 1.0). Roughness input = IRI (m/km). Blank inputs are not counted: only 10 m slots
# with at least one real value get a PCI; 100 m / 1 km PCI = mean of those 10 m PCI values.
PCI_WEIGHTS = {'cracking_pct': 0.12, 'ravelling_area': 0.10, 'pothole_area': 0.16,
               'patching_area': 0.08, 'rutting_mm': 0.14, 'roughness_iri': 0.40}

LANE_TO_DIRECTION = {**{f'L{i}': 'Increasing' for i in range(1, 9)},
                     **{f'R{i}': 'Decreasing' for i in range(1, 9)}}

# ── master_chainage ──────────────────────────────────────────────────────────
#   Lanes that ALWAYS get their own <lane>_start_chainage / _end_chainage / _start_lat / _start_long / _end_lat /
#   _end_long columns (NULL when the lane is not in D4). Any other lane that has data in D4 (L3, L4, R3 …) gets
#   the same 6 columns added automatically, in lane order (as the DLM notebook).
MASTER_CHAINAGE_LANES = ['L1', 'L2', 'R1', 'R2']
#   How lhs_* / rhs_* start/end lat-long are taken from the side's lanes in each 10 m row:
#     'lane_extent'→ as the DLM notebook: start lat/long of the lane with the lowest start chainage,
#                    end lat/long of the lane with the highest end chainage (tie → lowest lane number) (current choice)
#     'min_max'    → each coordinate on its own: MIN of the lanes' start lat / start long,
#                    MAX of the lanes' end lat / end long   (older meeting rule)
MASTER_CHAINAGE_SIDE_GPS = 'lane_extent'

# ── DLM LHS / RHS hierarchy tables (new DLM table names) ─────────────────────
#   parent (one row per side per 1 km) → its child tables. display_defect_id: parent breach rows D/{ddmmyy}/N/{%05d},
#   child breach rows {parent id}/{%04d}; a child breach row whose parent has no id gets its own D/{ddmmyy}/N/{%05d}.
#   Keys = table modules in tables/ (new_master_pothole writes the table 'master_pothole' — the NSV lane-wise
#   master_pothole keeps its name in dlm_kpi). Order = DLM numbering order (parents of every defect first).
#   All of these tables are written to <UPC>/dlm_kpi_new (as the DLM notebook's dlm_kpi_new schema).
# keys below = module names in tables/; the file names written are config.OUTPUT_NAMES (below)
HIERARCHY_TABLES = {
    'master_roughness': ['child_roughness_as_per_ca', 'child_10m_roughness'],
    'master_rutting': ['child_rutting_as_per_ca', 'child_10m_rutting'],
    'master_cracking_ca': ['child_10m_cracking'],
    'new_master_pothole': [],              # one row per pothole, no child table
}
#   Cracking: master_cracking_ca (→ master_cracking_as_per_ca) = top level, CA, D2 CA cracking reporting interval;
#   child_10m_cracking (→ master_cracking_10m) = child, CA + IRC, 10 m. Parent row per 'side' (LHS / RHS, value = average of the side's
#   lanes, as master_roughness) or per 'lane' (the lane's own CA row at the D2 CA interval,
#   lane-wise like child_roughness_as_per_ca / child_rutting_as_per_ca — default).
#   CRACKING_PARENT_FROM_D2 = True: master_cracking_ca block = D2 CA cracking interval (same rule as
#   child_<d>_as_per_ca); CRACKING_PARENT_M is then only the fallback when D2 has no CA interval and
#   CHILD_AS_PER_CA_DEFAULT_M is None. False → always CRACKING_PARENT_M (old fixed 100 m).
CRACKING_PARENT_FROM_D2 = True
CRACKING_PARENT_M = 100
CRACKING_PARENT_BY = 'lane'
#   Side parents whose column list also has the 8 CA / IRC columns of the 10 m table (written as NULL)
PARENT_EXTRA_COLUMNS = ['rutting']
HIERARCHY_FOLDER = 'dlm_kpi_new'

# ── The ONLY tables written (all to <UPC>/dlm_kpi_new). Every other table (NSV 10 m / 100 m / 1 km, NSV potholes,
#    thresholds, intervals) is still built in memory as the source of these and for the DQ / rule checks, but is
#    not written. Keys = table modules in tables/ (new_master_pothole writes 'master_pothole').
# Output file / collection names of the hierarchy tables (dlm_kpi_new), as in the DLM table list.
#   key (= module name in tables/, used inside the run and in OUTPUT_TABLES / HIERARCHY_TABLES) → output name.
#   Keys not listed keep their own name.
OUTPUT_NAMES = {
    'master_roughness': 'master_roughness_parent',            # LHS / RHS parent, CA, 1000 m
    'child_10m_roughness': 'master_roughness_child_10m',      # lane, CA + IRC, 10 m
    'child_roughness_as_per_ca': 'master_roughness_as_per_ca',  # lane, CA, D2 CA interval
    'master_rutting': 'master_rutting_parent',
    'child_10m_rutting': 'master_rutting_child_10m',
    'child_rutting_as_per_ca': 'master_rutting_as_per_ca',
    'master_cracking_ca': 'master_cracking_as_per_ca',        # lane, CA, top level, D2 CA interval
    'child_10m_cracking': 'master_cracking_10m',              # lane, CA + IRC, 10 m
    'new_master_pothole': 'master_pothole',                   # one row per pothole
}
OUTPUT_TABLES = [
    'master_roughness', 'child_roughness_as_per_ca', 'child_10m_roughness',
    'master_rutting', 'child_rutting_as_per_ca', 'child_10m_rutting',
    'master_cracking_ca', 'child_10m_cracking',
    'new_master_pothole',
    'master_chainage',
    'geometry_parent', 'geometry_child',
]
#   child_<defect>_as_per_ca is built at the D2 CA reporting interval ONLY ('Comparison as per CA':
#   'Current Cycle | <defect> | Reporting Interval', else 'Overall Reporting Interval'). The IRC sheet is never used
#   in place of CA (and CA never in place of IRC). No CA interval → this default + DQ gate FAIL.
#   Set to None to leave the table empty instead.
CHILD_AS_PER_CA_DEFAULT_M = 100

# ── Source order for point/area defects (first source that HAS data is used) ──
#   'defect_report' = D4 defect report 10 m columns
#   'nsv_cracks'    = D4 standalone NSV cracks sheet  (d4_nsv_cracks_cracks)
CRACKING_SOURCES = ['defect_report', 'nsv_cracks']
# Potholes: ONLY the D4 NHAI One pothole sheet (one POINT row per pothole; start = end chainage, length 0).
# No fallback: without an NHAI One sheet master_pothole is empty. rating_ca / rating_irc come from the
# D4 defect report 'Visual Condition - Pothole (CA)' / '(IRC)' of the pothole's lane + 10 m segment.

# ── D4 reading ───────────────────────────────────────────────────────────────
#   Some D4s give 'Visual Condition - Cracks (IRC)' / 'Pothole (IRC)' as a number 1–5 instead of text.
#   Such numbers are turned into these labels when D4 is read (text labels are kept as they are).
D4_VC_NUMBER_LABELS = {5: 'Very Good', 4: 'Good', 3: 'Fair', 2: 'Poor', 1: 'Very Poor'}
#   A D4 lane is used only when at least this share of the D4 rows has a value in that lane
#   (a lane with a few stray cells, e.g. 1 value in 2,810 rows, is skipped with a warning).
MIN_LANE_FILL = 0.01

# ── image names ──────────────────────────────────────────────────────────────
#   Cracking: ONLY the D4 crack image list (d4_images_crack_images.parquet; lane from the file name, e.g. '..._L1_...').
#   Each image goes to ONE 10 m cracking row of its lane — the first step that finds a row wins:
#     1) chainage (col H) inside the row's chainage range [start, end)
#     2) latitude / longitude (cols I, J = EXIF) within CRACK_IMAGE_TOLERANCE_M of the row's segment (nearest row)
#     3) ocr_chainage (col O) inside the row's range — only a plain number ('3:' is ignored)
#     4) ocr_latitude / ocr_longitude (cols P, Q; 'Lat:' inside ocr_longitude is read too) within the tolerance —
#        only values with ≥ 5 decimals ('28.57' is ignored)
#   Images left after the 4 steps are only counted in the run log (not a DQ / rule check).
#   image_name = file names, image_urls = image paths. 100 m / 1 km rows: all images of their 10 m rows.
#   Pothole: all distinct image names of the potholes on the same lane + chainage (NHAI One sheet).
#   Several names in one cell are joined with IMAGE_SEPARATOR; image_urls holds them as a list.
IMAGE_SEPARATOR = ', '
CRACK_IMAGE_TOLERANCE_M = 5.0

# ── master_pothole breach_flag / road_condition ──────────────────────────────
#   breach_flag = 0 when the pothole's SEVERITY starts with one of these words (case-insensitive), else 1.
#   Only the severity column is used (not type of defect). Blank severity → 1 (a recorded pothole).
#   road_condition comes ONLY from breach_flag: 1 → POTHOLE_ROAD_CONDITION[1], 0 → POTHOLE_ROAD_CONDITION[0].
POTHOLE_NO_BREACH_SEVERITY = ['low', 'delamination']
POTHOLE_ROAD_CONDITION = {1: 'Bad', 0: 'Good'}

# ── DQ checks against D2 ─────────────────────────────────────────────────────
DQ_PASS_PCT       = 98.0    # a check passes when at least this % of D2 rows match
DQ_TOLERANCE      = 0.01    # |D2 − silver| allowed for rut depth (mm), BI (mm/km), cracking (%)
# per-defect tolerance where D2 rounds: roughness BI is in mm/km (1,000–5,000), so 1 mm/km (< 0.1 %) forgives
# D2's rounding to whole numbers but still reports a real difference; every other defect uses DQ_TOLERANCE
DQ_TOLERANCE_BY_DEFECT = {'roughness': 1.0}
DQ_AREA_TOLERANCE = 0.01    # sq m, pothole area per lane per 100 m
DQ_POTHOLE_EXCLUDE_SEVERITY = ['delamination']   # D2 pothole rows not counted as potholes

# ── Roughness BI ─────────────────────────────────────────────────────────────
#   10 m : BI = 630 × IRI^1.12; IRI blank → D4 'Roughness BI' as it is; both blank → NULL (never IRI)
#   Threshold is BI mm/km (D2, else defect_rules.json). A D2 value in IRI m/km is converted to BI.
#   100 m / 1 km:
#     'iri_formula' → average the IRI, then 630 × IRI^1.12  (how D2 is built — matches D2; current choice)
#     'mean_bi'     → average of the BI values               (Core Columns sheet wording)
#   A block with no IRI at all but D4 BI values keeps the average of those BI values.
ROUGHNESS_AGGREGATION = 'iri_formula'

# ── 1 km (parent) averages ───────────────────────────────────────────────────
#   'from_10m'  → average of ALL the 10 m values in the km   (how D2 is built — matches D2; current choice)
#   'from_100m' → average of the 100 m averages in the km    (Core Columns sheet wording)
#   Only averages change (rut depth, IRI, BI). Ravelling sq m is a sum and gives the same result either way.
PARENT_AGGREGATION = 'from_10m'

# ── road_condition ───────────────────────────────────────────────────────────
#   Rutting / roughness / ravelling (every level, CA and IRC rows): ONLY from breach_flag —
#     1 → 'Poor', 0 → 'Good', blank metric_value or no D2 threshold → NULL. No D4 / severity / rating fallback.
#   Pothole: ONLY from breach_flag (see POTHOLE_ROAD_CONDITION).
#   Cracking only:
#     1) D4 'Visual Condition - <defect> (CA)/(IRC)' (CA rows CA label, IRC rows IRC label);
#        100 m / 1 km blocks take the WORST 10 m label
#     2) no D4 label: CA rows Good/Bad from breach, IRC rows blank (NULL when the % is blank or no D2 threshold)
#     (D3 is not used anywhere)
#   Order used to pick the worst label (D4 cracking / pothole) is below.
CONDITION_SEVERITY_ORDER = ['very poor', 'poor', 'fair', 'good', 'very good']   # worst first

# S3 location of the silver output (for reference). The code writes to OUTPUT_ROOT on the local / mounted disk;
# that folder is what goes to this S3 prefix (same zone=/phase=/cycle=/<UPC> layout).
SILVER_S3_ROOT = 's3://dhms-new-stgae/nsv-silver'
