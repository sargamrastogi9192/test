"""Silver files of one UPC: the dlm_kpi_new folder (or the UPC folder above it), local or s3://."""

import io
import os

import pandas as pd


def _s3_get(path: str) -> bytes | None:
    import boto3
    from botocore.exceptions import ClientError
    bucket, key = path[len("s3://"):].split("/", 1)
    try:
        return boto3.client("s3").get_object(Bucket=bucket, Key=key)["Body"].read()
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
            return None
        raise


def silver_folder(path: str) -> str:
    """Accepts the dlm_kpi_new folder or the UPC folder above it."""
    path = path.rstrip("/\\")
    if path.lower().endswith("dlm_kpi_new"):
        return path
    if path.startswith("s3://"):
        return path + "/dlm_kpi_new"
    sub = os.path.join(path, "dlm_kpi_new")
    return sub if os.path.isdir(sub) else path


def read_silver(folder: str, name: str) -> tuple[pd.DataFrame | None, str]:
    """(DataFrame or None, path tried). Parquet preferred, csv if there is no parquet."""
    for ext in ("parquet", "csv"):
        p = f"{folder}/{name}.{ext}" if folder.startswith("s3://") else os.path.join(folder, f"{name}.{ext}")
        if folder.startswith("s3://"):
            data = _s3_get(p)
            if data is None:
                continue
            return (pd.read_parquet(io.BytesIO(data)) if ext == "parquet" else pd.read_csv(io.BytesIO(data))), p
        if os.path.exists(p):
            return (pd.read_parquet(p) if ext == "parquet" else pd.read_csv(p, low_memory=False)), p
    return None, f"{folder}/{name}.parquet"
