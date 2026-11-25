# DHMS data push

Pushes the silver tables of **one survey** (UPC + phase + cycle) into the DHMS Postgres database.
Works with the output of both pipelines (`<UPC>/dlm_kpi_new/*.parquet`):

- the **new-format** silver (deliverables D1–D4; no ravelling tables)
- the **old-format** pipeline `dlm-extraction-pipeline` (backup lane files; with ravelling)

| Group | Database table | Silver file |
|---|---|---|
| Roughness | `master_roughness_parent`, `master_roughness_child_10m`, `master_roughness_as_per_ca` | same names |
| Rutting | `master_rutting_parent`, `master_rutting_child_10m`, `master_rutting_as_per_ca` | same names |
| Cracking | `master_cracking_child_10m`, `master_cracking_as_per_ca` | `master_cracking_10m`, `master_cracking_as_per_ca` |
| Pothole | `master_pothole` | same |
| Ravelling | `master_ravelling`, `master_ravelling_10m` | same (old format only) |
| Geometry | `geometry_parent`, `geometry_child` | same |

DL tables (`master_line_dl`, `child_line_dl`, `master_point`) are not included yet. The list is `TARGETS` in
`dhms_push/config.py`.

## What a run does

1. **Reads** the silver files of the survey and how each target table is defined in the database
   (exact column types, NOT NULL, defaults, keys, foreign keys, triggers). The definition of every table is written
   to the report as `ddl/<table>.sql`.
2. **Project ids from the selected database.** The silver tables do not carry the DHMS ids. The UPC is looked up
   in `master_upc_project` **of the database you push to** (`MASTER_PROJECT_TABLE`), on `upc`, else on
   `alternate_upcs`, ignoring case and `/ - _`. Its ids go into every table that has the column:

   | Table column | `master_upc_project` column |
   |---|---|
   | project_id | id |
   | region_id | region_id |
   | piu_id | piu_id |
   | state_id | state_id |
   | zone_id | nsv_zone_id |
   | contractor_id | contractor_id |
   | ae_ie_id | ae_ie_id |

   Push the same silver to another database and the ids of that database are used. UPC not in the master →
   BLOCKER, nothing is pushed. Exception (DLM rule): `geometry_parent.project_id` keeps the UPC text
   (`KEEP_SILVER_VALUE` in config.py).
3. **Checks** every table: BLOCKER (the insert would fail) / WARNING (it would insert, but not as expected) / INFO.
4. **Pushes, only with `--apply` and no BLOCKER**, in **one transaction for all tables**:
   - `--mode replace` (default): delete this survey's rows in each table (children first), then insert (parents
     first). Rows of other UPCs / phases / cycles are not touched.
   - `--mode append`: insert only (refused when a key already exists).

   Any error → everything is rolled back; the database is exactly as before.

Without `--apply` nothing is written (the check runs in a READ ONLY transaction).

### How a survey's rows are found (replace)

`upc_code` (else `upc`) + `phase` + `cycle`, whichever the table has, compared without case / separators.
`geometry_parent` has no phase / cycle and `geometry_child` has no UPC column (found by
`parent_id = <UPC>_geometry_parent`): geometry is per road, so a push replaces the UPC's geometry.
The report shows, per table, which columns were used.

### Values

Only the table's own columns are sent (silver-only columns are dropped, listed as WARNING). Values are converted to
the column type: numbers, booleans, dates, timestamps, JSON (`NaN` inside the JSON → `null`, which Postgres
needs), arrays (a single value such as the old pipeline's `sources = 'NSV'` → `{NSV}`), text (a silver list such as
`image_urls` → joined with `, `). Columns the database fills itself (identity, generated) are not sent.

## Run

```bash
python push.py --silver <UPC folder or its dlm_kpi_new folder, local or s3://…>          # dry run + report
python push.py --silver … --apply                                                      # push
python push.py --silver … --db dhms_production --schema public --apply                 # another database
python push.py --silver … --tables master_pothole,geometry_parent --apply              # some tables only
python push.py --silver … --mode append --apply
```

`--db` / `--schema` override `DB_NAME` / `DB_SCHEMA` in `.env` for that run.

Report: `reports/<time>_<db>_<UPC>/report.md` (start here), `summary.csv` (per table: rows to delete / insert,
blockers, warnings), `issues.csv`, `columns.csv`, `ids.json`, `ddl/<table>.sql`, `push_log.csv` (after `--apply`).

Exit code: 0 ok · 1 blockers or push failed (rolled back) · 2 cannot connect / no silver files.

## Setup

```bash
python -m venv venv
source venv/bin/activate              # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                  # DB_HOST, DB_NAME, DB_USER; DB_PASSWORD or DB_SECRET_NAME
```

On EC2: attach a role with `deploy/iam-policy.json` (read the silver bucket + the DB password secret), the RDS
security group must allow port 5432 from the instance, and use `./deploy/run.sh --silver … [--apply]`
(console output also goes to `logs/push_<date>.log`). The database user needs SELECT on the target tables and
`master_upc_project`, plus INSERT and DELETE on the target tables.

## Layout

```
dhms-data-push/
├── push.py                 # entry point
├── dhms_push/
│   ├── config.py           # .env, --db override, target tables, id columns, survey columns
│   ├── project_ids.py      # UPC → ids from master_upc_project of the selected database
│   ├── schema.py           # table definition from the database catalog
│   ├── compare.py          # silver vs definition: BLOCKER / WARNING / INFO
│   ├── survey.py           # which survey, and how its rows are found in each table
│   ├── prepare.py          # silver values → the column types for INSERT
│   └── silver.py           # read silver (local or S3)
├── deploy/run.sh, deploy/iam-policy.json
├── requirements.txt, .env.example
```
