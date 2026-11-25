"""Upload the breach images of one survey to the DHMS CDN and write the CDN urls into the silver tables.

Same as the old-format pipeline (src/output/image_uploader.py), for the new format:
  - tables:  master_pothole (defect_type 'pothole') and master_cracking_10m (defect_type 'cracking')
  - rows:    breach rows only (display_defect_id set) that have an image_name
  - files:   found by name in the bronze image lists d4_images_pothole_images / d4_images_crack_images
             (file_name -> image_path, the raw S3 file)
  - upload:  POST <IMAGE_UPLOAD_URL> (default https://nhaidhms-stage-api.dic.org.in/defects/images/upload),
             multipart: image=<file>, defect_type, display_defect_id; HTTP 201 -> data.url = the CDN url
  - result:  image_url = the first CDN url of the row, image_urls = every CDN url of the row (a row can hold several
             images, comma separated in image_name); the silver files (.parquet and .csv) are written back.

Images already uploaded for the same image + display_defect_id (image_uploads.csv next to the silver tables) are not
uploaded again on a re-run.

    python images/upload_images.py --zone B --phase 1 --cycle 1 --upc N/02015/03002/GJ
Settings (environment / .env, passed by run.py): BUCKET, BRONZE_PREFIX, S3_SILVER_INPUT_DIR, IMAGE_UPLOAD_URL,
IMAGE_UPLOAD_WORKERS. Exit code: 0 ok, 1 some uploads failed (the rest is saved), 2 could not run.
"""

import argparse
import io
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pandas as pd
import requests

DEFAULT_UPLOAD_URL = "https://nhaidhms-stage-api.dic.org.in/defects/images/upload"
TABLES = [("master_pothole", "pothole"), ("master_cracking_10m", "cracking")]
IMAGE_LISTS = {"pothole": "d4_images_pothole_images", "cracking": "d4_images_crack_images"}
SEPARATOR = ", "                              # silver config.IMAGE_SEPARATOR
CACHE_FILE = "image_uploads.csv"
RETRIES, RETRY_DELAY_SEC, TIMEOUT_SEC = 2, 1.0, 30


# ── files: S3 or local ────────────────────────────────────────────────────────
def _s3():
    import boto3
    return boto3.client("s3")


def split_s3(uri):
    bucket, _, key = uri[len("s3://"):].partition("/")
    return bucket, key


def list_files(root):
    """Every file under root (s3://… or a local folder)."""
    if root.startswith("s3://"):
        bucket, prefix = split_s3(root.rstrip("/") + "/")
        out = []
        for page in _s3().get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
            out += [f"s3://{bucket}/{o['Key']}" for o in page.get("Contents", [])]
        return out
    return [os.path.join(d, f) for d, _, fs in os.walk(root) for f in fs]


def read_bytes(path):
    if path.startswith("s3://"):
        bucket, key = split_s3(path)
        return _s3().get_object(Bucket=bucket, Key=key)["Body"].read()
    with open(path, "rb") as f:
        return f.read()


def write_bytes(path, data):
    if path.startswith("s3://"):
        bucket, key = split_s3(path)
        _s3().put_object(Bucket=bucket, Key=key, Body=data)
    else:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)


def exists(path):
    if path.startswith("s3://"):
        bucket, key = split_s3(path)
        try:
            _s3().head_object(Bucket=bucket, Key=key)
            return True
        except Exception:                                     # noqa: BLE001 — 404 / no access: not there
            return False
    return os.path.exists(path)


def read_table(path):
    data = io.BytesIO(read_bytes(path))
    return pd.read_parquet(data) if path.endswith(".parquet") else pd.read_csv(data, dtype=str, keep_default_na=False)


def write_parquet_and_csv(df, base):
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    write_bytes(base + ".parquet", buf.getvalue())
    write_bytes(base + ".csv", df.to_csv(index=False).encode("utf-8-sig"))


# ── image index from the bronze image lists ───────────────────────────────────
def image_index(bronze_part):
    """{'pothole': {name: path}, 'cracking': {name: path}} — name = file name and file name without extension."""
    files = list_files(bronze_part)
    index = {}
    for kind, list_name in IMAGE_LISTS.items():
        index[kind] = {}
        hits = [f for ext in (".parquet", ".csv") for f in sorted(files)
                if os.path.basename(f) == f"{list_name}{ext}"]            # parquet first
        if not hits:
            print(f"  {kind}: no {list_name} in {bronze_part} (no {kind} image folder delivered in D4)")
            continue
        df = read_table(hits[0])
        for name, path in zip(df["file_name"].astype(str), df["image_path"].astype(str)):
            if name and path:
                index[kind].setdefault(name.strip(), path.strip())
                index[kind].setdefault(os.path.splitext(name.strip())[0], path.strip())
        print(f"  {kind}: {len(set(index[kind].values())):,} images listed ({os.path.basename(hits[0])})")
    return index


def find_image(index, name, kind):
    """By name, then name without extension; own kind first, then the other (as the old uploader)."""
    stem = os.path.splitext(name)[0]
    other = "cracking" if kind == "pothole" else "pothole"
    return (index[kind].get(name) or index[kind].get(stem)
            or index[other].get(name) or index[other].get(stem))


# ── upload ────────────────────────────────────────────────────────────────────
class Uploader:
    def __init__(self, url):
        url = url.rstrip("/")
        self.url = url if url.endswith("/defects/images/upload") else f"{url}/defects/images/upload"
        self.session = requests.Session()

    def upload(self, path, kind, display_defect_id):
        """(ok, CDN url or error text). Same request as the old uploader."""
        safe_id = re.sub(r"[^A-Za-z0-9._-]", "-", display_defect_id)[:128]
        error = ""
        for attempt in range(1, RETRIES + 1):
            try:
                files = {"image": (os.path.basename(path), io.BytesIO(read_bytes(path)), "image/jpeg")}
                r = self.session.post(self.url, files=files, data={"defect_type": kind, "display_defect_id": safe_id},
                                      timeout=TIMEOUT_SEC)
                if r.status_code == 201:
                    url = (r.json().get("data") or {}).get("url", "")
                    if url:
                        return True, url
                    error = f"HTTP 201 without data.url: {r.text[:200]}"
                else:
                    error = f"HTTP {r.status_code}: {r.text[:200]}"
            except Exception as e:                            # noqa: BLE001 — reported per image
                error = f"{type(e).__name__}: {str(e)[:200]}"
            if attempt < RETRIES:
                time.sleep(RETRY_DELAY_SEC)
        return False, error


def names_of(cell):
    if cell is None or (isinstance(cell, float) and pd.isna(cell)):
        return []
    return [n.strip() for n in str(cell).split(SEPARATOR.strip()) if n.strip()]


def process_table(df, kind, index, uploader, cache, workers):
    """Upload the images of the breach rows; returns (table, summary). cache: {(name, display id): url}."""
    s = {"rows_with_images": 0, "images": 0, "already_uploaded": 0, "uploaded": 0, "no_file": 0, "failed": 0}
    if df.empty or "image_name" not in df or "display_defect_id" not in df:
        return df, s
    did = df["display_defect_id"].astype("string").fillna("").str.strip()
    todo_rows = df.index[(did != "") & df["image_name"].map(lambda v: bool(names_of(v)))]
    s["rows_with_images"] = len(todo_rows)
    jobs = {}                                                 # (name, display id) -> path
    for i in todo_rows:
        for name in names_of(df.at[i, "image_name"]):
            key = (name, did[i])
            if key in cache or key in jobs:
                continue
            path = find_image(index, name, kind)
            if path:
                jobs[key] = path
            else:
                s["no_file"] += 1
                print(f"    no file for image {name} ({did[i]})")
    needed = {(n, did[i]) for i in todo_rows for n in names_of(df.at[i, "image_name"])}
    s["images"] = len(needed)
    s["already_uploaded"] = sum(1 for k in needed if k in cache)

    def work(item):
        (name, d), path = item
        return (name, d), uploader.upload(path, kind, d)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for key, (ok, res) in pool.map(work, jobs.items()):
            if ok:
                cache[key] = res
                s["uploaded"] += 1
            else:
                s["failed"] += 1
                print(f"    upload FAILED {key[0]} ({key[1]}): {res}")

    out = df.copy()
    for col in ("image_url", "image_urls"):
        if col not in out:
            out[col] = None
    out["image_url"] = out["image_url"].astype(object)
    out["image_urls"] = out["image_urls"].astype(object)
    for i in todo_rows:
        urls = [cache[(n, did[i])] for n in names_of(out.at[i, "image_name"]) if (n, did[i]) in cache]
        if urls:
            out.at[i, "image_url"] = urls[0]
            out.at[i, "image_urls"] = urls
    return out, s


# ── one survey ────────────────────────────────────────────────────────────────
def part_of(zone, phase, cycle, upc):
    code = re.sub(r"[-/\s]+", "_", upc.strip()).upper()
    return f"Zone={zone}/Phase={phase}/Cycle={cycle}/UPC={code}"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    for k in ("zone", "phase", "cycle", "upc"):
        ap.add_argument(f"--{k}", required=True)
    a = ap.parse_args()
    part = part_of(a.zone, a.phase, a.cycle, a.upc)
    bucket = os.getenv("BUCKET", "dhms-new-stgae")
    silver_root = (os.getenv("S3_SILVER_INPUT_DIR") or f"s3://{bucket}/{os.getenv('SILVER_PREFIX', 'DHMS/NSV/Silver')}").rstrip("/")
    bronze_root = (os.getenv("BRONZE_ROOT") or f"s3://{bucket}/{os.getenv('BRONZE_PREFIX', 'DHMS/NSV/Bronze')}").rstrip("/")
    silver, bronze = f"{silver_root}/{part}", f"{bronze_root}/{part}"
    url = os.getenv("IMAGE_UPLOAD_URL") or DEFAULT_UPLOAD_URL
    workers = int(os.getenv("IMAGE_UPLOAD_WORKERS") or 4)
    print(f"Images of {part}\n  bronze {bronze}\n  silver {silver}/dlm_kpi_new\n  upload {url}")

    try:
        index = image_index(bronze)
    except Exception as e:                                    # noqa: BLE001
        print(f"Could not read the bronze image lists: {type(e).__name__}: {e}")
        return 2
    cache_path = f"{silver}/{CACHE_FILE}"
    cache_df = read_table(cache_path) if exists(cache_path) else pd.DataFrame(
        columns=["image_name", "display_defect_id", "defect_type", "cdn_url", "uploaded_at"])
    caches = {k: {(r.image_name, r.display_defect_id): r.cdn_url for r in cache_df[cache_df.defect_type == k].itertuples()
                  if r.cdn_url} for k in IMAGE_LISTS}

    uploader, failed = Uploader(url), 0
    for table, kind in TABLES:
        base = f"{silver}/dlm_kpi_new/{table}"
        if not exists(base + ".parquet"):
            print(f"  {table}: no silver file — skipped")
            continue
        df = read_table(base + ".parquet")
        out, s = process_table(df, kind, index, uploader, caches[kind], workers)
        print(f"  {table}: {s}")
        failed += s["failed"]
        if s["uploaded"] or s["already_uploaded"]:
            write_parquet_and_csv(out, base)
            print(f"  {table}: image_url / image_urls written ({base}.parquet + .csv)")

    # record of every uploaded image (kept rows keep their first upload time)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    when = {(r.image_name, r.display_defect_id, r.defect_type): r.uploaded_at for r in cache_df.itertuples()}
    rows = [{"image_name": n, "display_defect_id": d, "defect_type": k, "cdn_url": u,
             "uploaded_at": when.get((n, d, k), now)} for k, c in caches.items() for (n, d), u in c.items()]
    if rows:
        write_bytes(cache_path, pd.DataFrame(rows).to_csv(index=False).encode("utf-8-sig"))
    print(f"Done: {failed} failed upload(s)" if failed else "Done: every image uploaded")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
