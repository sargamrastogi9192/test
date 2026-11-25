# DHMS NSV pipeline (new-format surveys)

One repository, one settings file, one command for the whole flow:

```
raw deliverables (S3) ─► bronze ─► silver ─► images ─► push_silver ─► gold ─► push_gold
                          (S3)      (S3)      CDN urls   DLM tables      (S3)    ris_view_kpi /
                                              in silver  in the DB               ris_trends_kpi in the DB
```

| Folder | Stage | Per | What it does |
|---|---|---|---|
| `bronze/` | `bronze` | survey | raw vendor deliverables D1–D4 → standardised bronze tables in S3 |
| `silver/` | `silver` | survey | bronze → silver tables (`<UPC>/dlm_kpi_new/`): 10 m / CA interval / LHS-RHS 1 km, breach flags, display ids |
| `images/` | `images` | survey | breach images of `master_pothole` / `master_cracking_10m` → CDN (`IMAGE_UPLOAD_URL`, the stage API as in the old pipeline); the CDN urls go into `image_url` / `image_urls` of the silver tables before the push. Files found by name in the bronze image lists (D4 pothole / crack image folders); an image already uploaded for the same display id is not uploaded again |
| `push_silver/` | `push_silver` | survey | silver tables → DLM tables in the DB; the UPC's ids (project_id, region_id, …) from `master_upc_project` of that DB; checked against the DB table definitions first |
| `gold/` | `gold` | run | case statuses + RIS KPIs → `ris_view_data`, `ris_trends_data` in S3; then the DB ids of rows already in the DB (`sync-db-ids`) |
| `push_gold/` | `push_gold` | run | KPIs → `ris_view_kpi`, `ris_trends_kpi` |
| `pipeline/`, `run.py` | — | — | order, controls, logs |

Each folder keeps its own README with the details of its logic. Gold reads the DB tables (statuses, `master_upc_project`,
survey ids) from the copy the existing DB export job writes to S3 (`S3_DB_TABLES_DIR`).

## Silver rules, in short

| | 10 m | CA interval (lane-wise, D2 interval) | LHS / RHS 1 km parent |
|---|---|---|---|
| Roughness | BI = 630 × IRI^1.12 ≥ CA limit | average IRI → BI ≥ CA limit | value: pooled side BI (all 10 m rows of the side's lanes); **breach: any lane's CA row in the km breaches** |
| Rutting | rut ≥ CA limit | average rut ≥ CA limit | value: pooled side rut; breach: any lane's CA row breaches |
| Cracking | % ≥ CA limit | average % ≥ CA limit (top level) | — |
| Pothole | breach unless severity Low / Delamination | — | — |

IRC is on the 10 m rows only (columns on the same row). Details: `silver/README.md`.

Everything comes from the survey's own deliverables — no NSV Activity Tracker:
- **survey start / end date:** earliest / latest D1 LRP master *Survey Date* (else the D4 NHAI One pothole *Datetime*);
  `detected_on` = the end date, the same on every table of the survey (gold groups a survey by it), also the date in
  `display_defect_id`. Dates are read as ISO (`2026-01-08`) or day first (`08-01-2026`);
- **thresholds and reporting intervals:** D2;
- **CA / IRC standard:** CA when D2 has the *Comparison as per CA* sheet (IRC only when D2 has just the IRC one).

## Controls

| | Scheduled (`--scheduled`) | Manual (default) |
|---|---|---|
| step OK | next step | next step |
| step WARNING (bronze exit 2, silver exit 1, images exit 1) | **stop** | asks *Continue?* |
| step FAILED | **stop** | **stop** |
| database write | only after a clean check | shows the check, asks *Write to database …?* |

- Every database stage runs its **check first** (the push's dry run); the write runs only without blockers, in one
  transaction (an error rolls it back).
- `--no-write`: everything, database checks too, never writes.
- A stop skips everything after it; `--from <stage>` restarts there. One run at a time (lock file).
- Re-running a survey replaces its rows (silver tables in the DB) / updates them (gold, KPI tables): never doubled.
  Case statuses of surveys already in gold are kept; a new survey starts with every defect Open.

## Run

```bash
source .venv/bin/activate
python run.py                                         # surveys.csv, manual
python run.py --scheduled                             # unattended (cron, deploy/cron.example)
python run.py --zone B --phase 1 --cycle 1 --upc N/02015/03002/GJ
python run.py --from push_silver                      # start at a stage; also --to, --only
python run.py --no-write                              # everything except the database writes
python run.py --db dhms_uat                           # database of both pushes (else DB_NAME)
```

`surveys.csv`: `zone, phase, cycle, upc` (UPC with `/`, `_` or `-`).
Exit code: 0 every step OK · 1 stopped · 2 settings problem (listed before anything runs).
Logs: `logs/run_<time>/pipeline.log` (all output) and `summary.csv`; copied to `LOG_S3_URI` when set.

## Setup (EC2)

```bash
cd /root/dhms
git clone <repo-url> dhms-nsv-pipeline && cd dhms-nsv-pipeline
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
sudo apt install -y tesseract-ocr          # bronze OCR for images without GPS (optional, warned when missing)
cp .env.example .env && nano .env          # DB_PASSWORD (or DB_SECRET_NAME), DB_NAME, S3 locations, STAGES
nano surveys.csv
python run.py --no-write                   # first run: everything except the database writes
```

The EC2 role needs: S3 read on the raw bucket, read/write on `DHMS/NSV/Bronze|Silver|Gold/`, read on
`NSV/DHMS/DIC_Staging/Latest/`, and `secretsmanager:GetSecretValue` when `DB_SECRET_NAME` is used. The DB user needs
SELECT on `master_upc_project`, SELECT / INSERT / DELETE on the DLM tables, SELECT / INSERT / UPDATE on the KPI tables.

## Settings (`.env`, one file for every stage)

| Keys | Used by |
|---|---|
| `BUCKET`, `REGION`, `RAW_S3_ROOT`, `BRONZE_PREFIX`, `SILVER_PREFIX`, `MASTER_PROJECT_S3`, `WORK_DIR` | bronze, silver |
| `IMAGE_UPLOAD_URL`, `IMAGE_UPLOAD_WORKERS` | images |
| `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_SCHEMA`, `DB_PASSWORD` / `DB_SECRET_NAME`, `MASTER_PROJECT_TABLE` | push_silver, push_gold |
| `RIS_KPI_STORAGE`, `S3_DB_TABLES_DIR`, `S3_SILVER_INPUT_DIR`, `S3_GOLD_OUTPUT_DIR`, `DROP_ROWS_WITHOUT_IDS` | gold, push_gold |
| `STAGES`, `SURVEYS_FILE`, `LOG_DIR`, `LOG_S3_URI` | run.py |

`S3_SILVER_INPUT_DIR` must be the folder silver writes to (`s3://BUCKET/SILVER_PREFIX`): push_silver and gold read the
silver tables there. `run.py` resolves the DB password once (`DB_PASSWORD`, else the Secrets Manager secret) and hands
it to both pushes.

## Tests

```bash
python -m pytest -q tests                         # run rules (fake stages: no AWS, no DB)
cd gold && python -m pytest -q                    # gold logic
python tools/make_test_upc.py                     # a fake survey in the bronze format, to test silver without real data
```
