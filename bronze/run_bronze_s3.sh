
#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Run the NSV bronze extraction on EC2 for ONE UPC:
# raw vendor download in S3 → bronze in S3.
#
#   input : <RAW_S3_ROOT>/Zone A/Phase 2/Cycle 2/N_02001_19004_DL/<road>/Deliverable 1..4 .../
#   output: s3://<BUCKET>/DHMS/NSV/Bronze/Zone=A/Phase=2/Cycle=2/UPC=N_02001_19004_DL/d1..d4/   (overwritten, no run= folder)
#           (exactly where silver/run_silver_s3.sh reads from)
#
# Usage:
#   ./run_bronze_s3.sh --zone A --phase 2 --cycle 2 --upc N/02001/19004/DL --raw-root s3://bucket/raw/prefix/
#   ./run_bronze_s3.sh ... d1 d2 d4
#
# run_download.py is NOT changed: it reads raw files straight from S3 (boto3)
# and writes bronze to NSV_OUTPUT. Credentials come from the EC2 IAM role.
# Exit code: 0 = all extracted, 2 = extracted with problems, other = failed.
# ─────────────────────────────────────────────────────────────────────────────

set -uo pipefail

BUCKET="${BUCKET:-dhms-new-stgae}"
BRONZE_PREFIX="${BRONZE_PREFIX:-DHMS/NSV/Bronze}"
REGION="${REGION:-ap-south-1}"
RAW_S3_ROOT="${RAW_S3_ROOT:-}"

ZONE="" PHASE="" CYCLE="" UPC=""
EXTRA=()

CODE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PYTHON:-$CODE_DIR/../.venv/bin/python}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --zone)     ZONE="$2"; shift 2 ;;
    --phase)    PHASE="$2"; shift 2 ;;
    --cycle)    CYCLE="$2"; shift 2 ;;
    --upc)      UPC="$2"; shift 2 ;;
    --raw-root) RAW_S3_ROOT="$2"; shift 2 ;;
    -h|--help)  sed -n 2,17p "$0"; exit 0 ;;
    *)          EXTRA+=("$1"); shift ;;
  esac
done

[[ -n "$ZONE" && -n "$PHASE" && -n "$CYCLE" && -n "$UPC" ]] || {
  echo "ERROR: --zone --phase --cycle --upc are required"
  exit 2
}

[[ "$RAW_S3_ROOT" == s3://* && "$RAW_S3_ROOT" != *CHANGE-ME* ]] || {
  echo "ERROR: RAW_S3_ROOT / --raw-root must be the s3:// root of the raw download"
  exit 2
}

[[ -x "$PYTHON" ]] || PYTHON="$(command -v python3)"

UPC_DASH="$(echo "$UPC" | tr '/_ ' '---' | tr '[:lower:]' '[:upper:]')"
UPC_CODE="$(echo "$UPC_DASH" | tr '-' '_')"            # N_02001_19004_DL (bronze / silver folder name)

export NSV_OUTPUT="s3://${BUCKET}/${BRONZE_PREFIX}"
export AWS_DEFAULT_REGION="$REGION"

# Same switches as the production bronze runs.
export NSV_CHECK_HEADERS="${NSV_CHECK_HEADERS:-0}"
export NSV_STOP_IF_WORKBOOK_MISSING="${NSV_STOP_IF_WORKBOOK_MISSING:-0}"

S3_OUT="${NSV_OUTPUT}/Zone=${ZONE}/Phase=${PHASE}/Cycle=${CYCLE}/UPC=${UPC_CODE}/"

command -v tesseract >/dev/null || echo "WARNING: tesseract not installed - images without EXIF GPS get no location (sudo apt install -y tesseract-ocr)"

echo "════════════════════════════════════════════════════════════════════"
echo " BRONZE  : ${UPC}  (zone=${ZONE} phase=${PHASE} cycle=${CYCLE})"
echo " raw     : ${RAW_S3_ROOT}"
echo " output  : ${S3_OUT}"
echo " switches: NSV_CHECK_HEADERS=${NSV_CHECK_HEADERS} NSV_STOP_IF_WORKBOOK_MISSING=${NSV_STOP_IF_WORKBOOK_MISSING}"
echo " python  : ${PYTHON}"
echo "════════════════════════════════════════════════════════════════════"

cd "$CODE_DIR"

# No --use-found: a UPC found under another zone/phase/cycle must fail here.
"$PYTHON" run_download.py --root "$RAW_S3_ROOT" "${EXTRA[@]}" --zone "$ZONE" --phase "$PHASE" --cycle "$CYCLE" --upc "$UPC"

RC=$?

echo -e "\nBronze folders now in S3:"

for d in d1 d2 d3 d4; do
  if aws s3 ls "${S3_OUT}${d}/" --region "$REGION" >/dev/null 2>&1; then
    echo "  ${d}: ok"
  else
    echo "  ${d}: MISSING"
  fi
done

echo "run_download.py exit code: ${RC}  (0 = all ok, 2 = see data batch report / logs, other = failed)"

exit $RC
