
#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Run the NSV silver layer on EC2 for ONE UPC stored in S3.
#
#   input : s3://dhms-new-stgae/DHMS/NSV/Bronze/Zone=A/Phase=2/Cycle=1/UPC=N_02007_11001_HR/
#   output: s3://dhms-new-stgae/DHMS/NSV/Silver/Zone=A/Phase=2/Cycle=1/UPC=N_02007_11001_HR/
#           (overwritten: files of an earlier run that this run did not write are deleted)
#
# Usage:
#   ./run_silver_s3.sh
#   ./run_silver_s3.sh --zone A --phase 2 --cycle 1 --upc N-02007-11001-HR   (or N/02007/11001/HR, N_02007_11001_HR)
#   ./run_silver_s3.sh --upc N-02007-11001-HR --tracker s3://bucket/path/NSV_Activity_Tracker.xlsx
#   ./run_silver_s3.sh --excel
#   ./run_silver_s3.sh --no-csv
#
# main.py is NOT changed: this script downloads the UPC to local disk,
# runs main.py and uploads the output to DHMS/NSV/Silver/.
# ─────────────────────────────────────────────────────────────────────────────

set -uo pipefail

# ── defaults ─────────────────────────────────────────────────────────────────
BUCKET="${BUCKET:-dhms-new-stgae}"
BRONZE_PREFIX="${BRONZE_PREFIX:-DHMS/NSV/Bronze}"
SILVER_PREFIX="${SILVER_PREFIX:-DHMS/NSV/Silver}"
REGION="${REGION:-ap-south-1}"

ZONE="A"
PHASE="2"
CYCLE="1"
UPC="N-02007-11001-HR"

# Tracker path comes from pipeline.env, unless overridden by --tracker
TRACKER_S3="${TRACKER_S3:-}"
# Project master (ids) from pipeline.env, unless overridden by --master-project
MASTER_PROJECT_S3="${MASTER_PROJECT_S3:-}"

EXCEL=0
CSV=1
PULL_PREVIOUS=1

WORK_DIR="${WORK_DIR:-$HOME/nsv_work}"
CODE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PYTHON:-$CODE_DIR/.venv/bin/python}"
[[ -x "$PYTHON" ]] || PYTHON="$CODE_DIR/../.venv/bin/python"

# ── arguments ────────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --zone)    ZONE="$2"; shift 2 ;;
    --phase)   PHASE="$2"; shift 2 ;;
    --cycle)   CYCLE="$2"; shift 2 ;;
    --upc)     UPC="$2"; shift 2 ;;
    --bucket)  BUCKET="$2"; shift 2 ;;
    --tracker) TRACKER_S3="$2"; shift 2 ;;
    --master-project) MASTER_PROJECT_S3="$2"; shift 2 ;;
    --excel)   EXCEL=1; shift ;;
    --csv)     CSV=1; shift ;;
    --no-csv)  CSV=0; shift ;;
    --no-previous) PULL_PREVIOUS=0; shift ;;
    -h|--help) sed -n 2,19p "$0"; exit 0 ;;
    *) echo "Unknown option: $1"; exit 2 ;;
  esac
done

[[ -x "$PYTHON" ]] || PYTHON="$(command -v python3)"
command -v aws >/dev/null || {
  echo "ERROR: aws CLI not installed"
  exit 2
}

# N/02007/11001/HR, N-02007-11001-HR or N_02007_11001_HR → N_02007_11001_HR
UPC_FOLDER="$(echo "$UPC" | tr '/\- ' '___' | tr '[:lower:]' '[:upper:]')"
UPC_FOLDER="${UPC_FOLDER#UPC=}"
PART="Zone=${ZONE}/Phase=${PHASE}/Cycle=${CYCLE}/UPC=${UPC_FOLDER}"

# Bronze input and Silver output
S3_IN="s3://${BUCKET}/${BRONZE_PREFIX}/${PART}/"
S3_OUT_ROOT="s3://${BUCKET}/${SILVER_PREFIX}"
S3_OUT="${S3_OUT_ROOT}/${PART}/"


# Local staging folders
LOCAL_IN="${WORK_DIR}/bronze/nsv/${PART}"
LOCAL_OUT_ROOT="${WORK_DIR}/silver"
LOCAL_OUT="${LOCAL_OUT_ROOT}/${PART}"

echo "════════════════════════════════════════════════════════════════════"
echo " UPC     : ${UPC} (zone=${ZONE} phase=${PHASE} cycle=${CYCLE})"
echo " input   : ${S3_IN}"
echo " output  : ${S3_OUT}"
echo " tracker : ${TRACKER_S3:-Not configured}"
echo " master  : ${MASTER_PROJECT_S3:-Not configured}"
echo " work dir: ${WORK_DIR}"
echo " python  : ${PYTHON}"
echo "════════════════════════════════════════════════════════════════════"

# ── 1. Check the UPC exists in Bronze S3 ─────────────────────────────────────
if ! aws s3 ls "${S3_IN}" --region "$REGION" >/dev/null 2>&1; then
  echo "ERROR: nothing found at ${S3_IN}"
  echo "Check zone / phase / cycle / UPC and the EC2 IAM role."
  exit 3
fi

# ── 2. Download Bronze UPC ───────────────────────────────────────────────────
rm -rf "${LOCAL_IN}" "${LOCAL_OUT}"
mkdir -p "${LOCAL_IN}" "${LOCAL_OUT_ROOT}"

echo -e "\n[1/4] Downloading Bronze..."

aws s3 sync "${S3_IN}" "${LOCAL_IN}" --region "$REGION" --only-show-errors \
    --exclude "*.jpg" --exclude "*.jpeg" --exclude "*.png" \
    --exclude "*.JPG" --exclude "*.PNG" || {
  echo "ERROR: download failed"
  exit 4
}

for d in d1 d2 d3 d4; do
  [[ -d "${LOCAL_IN}/${d}" ]] && echo "  ${d}: ok" || echo "  ${d}: MISSING"
done

# ── 3. Optional NSV Activity Tracker ─────────────────────────────────────────
TRACKER_ARG=()

if [[ -n "$TRACKER_S3" ]]; then
  mkdir -p "${WORK_DIR}/tracker"
  TRACKER_LOCAL="${WORK_DIR}/tracker/$(basename "$TRACKER_S3")"

  if aws s3 cp "$TRACKER_S3" "$TRACKER_LOCAL" \
      --region "$REGION" --only-show-errors; then
    TRACKER_ARG=(--tracker "$TRACKER_LOCAL")
    echo "  Tracker downloaded successfully."
  else
    echo "ERROR: Tracker download failed: ${TRACKER_S3}"
    exit 4
  fi
else
  TRACKER_ARG=(--tracker "${WORK_DIR}/tracker/none.xlsx")
  echo "  WARNING: no TRACKER_S3 configured."
  echo "  CA standard / 100 m interval / survey date will be taken from data."
fi

# ── 4. Fetch previous UPC Silver tables ──────────────────────────────────────
if [[ "$PULL_PREVIOUS" == 1 ]]; then
  echo -e "\n[2/4] Fetching existing Silver tables for display_defect_id counter..."

  aws s3 sync "${S3_OUT_ROOT}/" "${LOCAL_OUT_ROOT}/" \
      --region "$REGION" --only-show-errors \
      --exclude "*" \
      --include "*/dlm_kpi/master_*.parquet" \
      --include "*/dlm_kpi_new/*.parquet" \
      --exclude "*/dlm_kpi/master_chainage.parquet" \
      --exclude "*/dlm_kpi_new/master_chainage.parquet" \
      --exclude "*/dlm_kpi_new/geometry_*.parquet" \
      --exclude "${PART}/*" \
      || echo "  WARNING: could not read previous Silver tables — counter starts at 1"
else
  echo -e "\n[2/4] Skipped fetching other UPCs (--no-previous)"
fi

# ── 5. Run Silver code ───────────────────────────────────────────────────────
echo -e "\n[3/4] Running Silver..."

# ── Optional project master (project_id, region_id, piu_id, state_id, zone_id, contractor_id, ae_ie_id) ──
MASTER_ARG=()
if [[ -n "$MASTER_PROJECT_S3" ]]; then
  mkdir -p "${WORK_DIR}/master"
  MASTER_LOCAL="${WORK_DIR}/master/$(basename "$MASTER_PROJECT_S3")"
  if aws s3 cp "$MASTER_PROJECT_S3" "$MASTER_LOCAL" --region "$REGION" --only-show-errors; then
    MASTER_ARG=(--master-project "$MASTER_LOCAL")
    echo "  Project master downloaded successfully."
  else
    echo "  WARNING: project master download failed: ${MASTER_PROJECT_S3}"
    echo "  project_id / region_id / piu_id / state_id / zone_id / contractor_id / ae_ie_id will be NULL."
  fi
else
  echo "  WARNING: no MASTER_PROJECT_S3 configured — project ids stay NULL."
fi

EXTRA=()
[[ "$EXCEL" == 0 ]] && EXTRA+=(--no-excel)
[[ "$CSV" == 1 ]] && EXTRA+=(--csv)

echo "  formats: parquet$([[ $EXCEL == 1 ]] && echo ' + excel')$([[ $CSV == 1 ]] && echo ' + csv')"

cd "$CODE_DIR"

"$PYTHON" main.py \
    --project "${LOCAL_IN}" \
    --out "${LOCAL_OUT_ROOT}" \
    "${TRACKER_ARG[@]}" \
    "${MASTER_ARG[@]}" \
    "${EXTRA[@]}"

RC=$?

# ── 6. Upload Silver output ──────────────────────────────────────────────────
echo -e "\n[4/4] Uploading to ${S3_OUT}..."

if [[ ! -d "${LOCAL_OUT}" ]]; then
  echo "ERROR: no output folder ${LOCAL_OUT}"
  exit 5
fi

# --delete: the UPC folder is overwritten (tables / files of an earlier run that are not written now go away);
# only this UPC's folder is synced, nothing else in Silver is touched
aws s3 sync "${LOCAL_OUT}/" "${S3_OUT}" \
    --region "$REGION" --only-show-errors --delete || {
  echo "ERROR: upload failed"
  exit 6
}

LATEST_BATCH="$(ls -t "${LOCAL_OUT_ROOT}/_batches/"batch=*.csv 2>/dev/null | head -1)"

if [[ -n "$LATEST_BATCH" ]]; then
  aws s3 cp "$LATEST_BATCH" "${S3_OUT_ROOT}/_batches/" \
      --region "$REGION" --only-show-errors
fi

echo -e "\nUploaded:"
aws s3 ls "${S3_OUT}" --recursive --region "$REGION" \
    | awk '{print "  " $4}' \
    | sed "s#${SILVER_PREFIX}/##"

echo -e "\nmain.py exit code: ${RC} (0 = all ok, 1 = see run.log / batch csv)"

exit $RC
