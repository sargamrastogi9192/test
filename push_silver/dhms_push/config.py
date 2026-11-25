"""Settings: database connection (.env, password from Secrets Manager), the target tables and the project ids.

--db / --schema on the command line override DB_NAME / DB_SCHEMA from .env.
"""

import json
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


# ── Target tables: (database table, silver file in <UPC>/dlm_kpi_new without .parquet, group) ──────────────
# Order = insert order (parents before their children); deletes run in the reverse order.
# Both pipelines write these files: the old-format pipeline (dlm-extraction-pipeline, also ravelling) and the
# new-format silver (silver_final, no ravelling). A file a pipeline does not write is skipped.
TARGETS = [
    ("master_roughness_parent", "master_roughness_parent", "Roughness"),
    ("master_rutting_parent", "master_rutting_parent", "Rutting"),
    ("master_cracking_as_per_ca", "master_cracking_as_per_ca", "Cracking"),
    ("master_ravelling", "master_ravelling", "Ravelling"),
    ("master_pothole", "master_pothole", "Pothole"),
    ("geometry_parent", "geometry_parent", "Geometry"),
    ("master_roughness_as_per_ca", "master_roughness_as_per_ca", "Roughness"),
    ("master_roughness_child_10m", "master_roughness_child_10m", "Roughness"),
    ("master_rutting_as_per_ca", "master_rutting_as_per_ca", "Rutting"),
    ("master_rutting_child_10m", "master_rutting_child_10m", "Rutting"),
    ("master_cracking_child_10m", "master_cracking_10m", "Cracking"),     # silver name differs
    ("master_ravelling_10m", "master_ravelling_10m", "Ravelling"),
    ("geometry_child", "geometry_child", "Geometry"),
    # DL (master_line_dl, child_line_dl, master_point): not included yet
]

# ── Project ids: column in the pushed tables ← column of the project master row of the UPC ───────────────
# The master is read from the SAME database the push goes to, so the ids always belong to that database.
ID_COLUMNS = {
    "project_id": "id",
    "region_id": "region_id",
    "piu_id": "piu_id",
    "state_id": "state_id",
    "zone_id": "nsv_zone_id",
    "contractor_id": "contractor_id",
    "ae_ie_id": "ae_ie_id",
}
# Master columns that identify the UPC: exact UPC column first, then a column listing other spellings.
MASTER_UPC_COLUMN = "upc"
MASTER_ALT_UPC_COLUMN = "alternate_upcs"
# (table, column) pairs that keep the silver value instead of the master id.
# DLM: geometry_parent.project_id is the UPC text, not the master id.
KEEP_SILVER_VALUE = {("geometry_parent", "project_id")}

# ── Rows of a survey (what "replace" deletes before inserting): the first UPC column the table has,
#    plus phase and cycle when the table has them.
UPC_COLUMNS = ["upc_code", "upc"]
SURVEY_COLUMNS = ["phase", "cycle"]
# Tables without a UPC column: their rows are found by another column holding a UPC-based value.
# geometry_child.parent_id = '<UPC>_geometry_parent' (geometry is per road, not per phase / cycle).
SCOPE_BY_COLUMN = {"geometry_child": ("parent_id", "{upc}_geometry_parent")}

# Silver writes some text columns as lists (e.g. image_urls); into a text column they go joined with this.
LIST_SEPARATOR = ", "
INSERT_PAGE_SIZE = 1000


@dataclass
class DbSettings:
    host: str
    port: int
    name: str
    user: str
    password: str
    schema: str
    region: str
    master_table: str


def _secret_password(secret_name: str, region: str) -> str:
    import boto3

    value = boto3.client("secretsmanager", region_name=region).get_secret_value(SecretId=secret_name)["SecretString"]
    try:
        return json.loads(value)["password"]           # RDS-managed secret: {"username": …, "password": …}
    except (json.JSONDecodeError, KeyError, TypeError):
        return value


def load_db_settings(db_name: str | None = None, schema: str | None = None) -> DbSettings:
    region = os.getenv("AWS_REGION", "ap-south-1")
    secret = os.getenv("DB_SECRET_NAME", "").strip()
    name = (db_name or os.getenv("DB_NAME", "")).strip()
    if not name:
        raise SystemExit("No database name: set DB_NAME in .env or pass --db.")
    return DbSettings(
        host=os.environ["DB_HOST"],
        port=int(os.getenv("DB_PORT", "5432")),
        name=name,
        user=os.environ["DB_USER"],
        password=_secret_password(secret, region) if secret else os.getenv("DB_PASSWORD", ""),
        schema=(schema or os.getenv("DB_SCHEMA", "public")).strip(),
        region=region,
        master_table=os.getenv("MASTER_PROJECT_TABLE", "master_upc_project").strip(),
    )


def get_engine(db: DbSettings):
    from sqlalchemy import create_engine
    from sqlalchemy.engine import URL

    return create_engine(URL.create("postgresql+psycopg2", username=db.user, password=db.password,
                                    host=db.host, port=db.port, database=db.name))
