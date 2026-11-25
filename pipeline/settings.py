"""One settings file for every stage: <repo>/.env (KEY=VALUE, # comments, ${OTHER_KEY} allowed).
An environment variable of the same name wins. Every value is passed on to every stage, already expanded, so the
stages (which read the same .env themselves) all see the same settings.
"""

import importlib.util
import json
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]                       # the repository
CODE = Path(os.getenv("PIPELINE_CODE_ROOT") or ROOT)             # stage folders (tests point this to fake stages)
ENV_FILE = Path(os.getenv("PIPELINE_ENV") or ROOT / ".env")

ALL_STAGES = ["bronze", "silver", "images", "push_silver", "gold", "push_gold"]
DB_STAGES = {"push_silver", "push_gold"}
PACKAGES = {"pandas": "pandas", "pyarrow": "pyarrow", "openpyxl": "openpyxl", "boto3": "boto3", "requests": "requests",
            "sqlalchemy": "sqlalchemy", "psycopg2": "psycopg2-binary", "dotenv": "python-dotenv"}


def read_env_file(path: Path) -> dict:
    raw = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            raw[key.strip()] = value.split(" #", 1)[0].strip().strip('"').strip("'")
    values = {**raw, **{k: os.environ[k] for k in raw if k in os.environ}}       # environment wins
    for _ in range(5):                                                            # ${A} may refer to ${B}
        values = {k: _expand(v, values) for k, v in values.items()}
    return values


def _expand(value: str, known: dict) -> str:
    for key, val in known.items():
        value = value.replace("${" + key + "}", val)
    return os.path.expandvars(os.path.expanduser(value))


@dataclass
class Settings:
    values: dict = field(default_factory=dict)

    def get(self, key, default=""):
        return (os.environ.get(key) or self.values.get(key) or default).strip()

    @property
    def db_name(self):
        return self.get("DB_NAME")

    @property
    def stages(self):
        return [s.strip() for s in self.get("STAGES", ",".join(ALL_STAGES)).split(",") if s.strip()]

    @property
    def silver_s3(self):
        """Where push_silver and gold read the silver tables: S3_SILVER_INPUT_DIR, else s3://BUCKET/SILVER_PREFIX
        (the place the silver stage writes to; the two must be the same folder)."""
        return self.get("S3_SILVER_INPUT_DIR").rstrip("/") or \
            f"s3://{self.get('BUCKET', 'dhms-new-stgae')}/{self.get('SILVER_PREFIX', 'DHMS/NSV/Silver')}"

    def path(self, key, default):
        p = Path(self.get(key, default)).expanduser()
        return p if p.is_absolute() else ROOT / p

    def stage_env(self) -> dict:
        """Environment of every stage: every .env value (expanded) + values derived for the stages."""
        env = dict(self.values)
        env.update({
            "PYTHON": sys.executable,                                  # bronze / silver scripts use this Python
            "AWS_DEFAULT_REGION": self.get("REGION", "ap-south-1"),
            "AWS_REGION": self.get("AWS_REGION", self.get("REGION", "ap-south-1")),
            "S3_SILVER_INPUT_DIR": self.silver_s3,                     # gold reads silver here
            "RIS_KPI_ROOT": str(ROOT),
            "PYTHONPATH": os.pathsep.join(p for p in [str(CODE / "gold" / "src"), os.environ.get("PYTHONPATH", "")] if p),
        })
        if self.db_name:
            env["DB_NAME"] = self.db_name
        return env


def load_settings(db_name: str | None = None) -> Settings:
    s = Settings(read_env_file(ENV_FILE))
    if db_name:
        s.values["DB_NAME"] = db_name
        os.environ["DB_NAME"] = db_name
    return s


def resolve_db_password(s: Settings) -> str | None:
    """Both pushes get the password as DB_PASSWORD: from .env, else from Secrets Manager (DB_SECRET_NAME).
    Returns a problem text, or None."""
    if s.get("DB_PASSWORD"):
        return None
    secret = s.get("DB_SECRET_NAME")
    if not secret:
        return "DB_PASSWORD and DB_SECRET_NAME are both empty in .env"
    try:
        import boto3
        value = boto3.client("secretsmanager", region_name=s.get("AWS_REGION", s.get("REGION", "ap-south-1"))) \
            .get_secret_value(SecretId=secret)["SecretString"]
    except Exception as e:                                             # noqa: BLE001 — reported, run not started
        return f"DB_SECRET_NAME {secret}: {type(e).__name__}: {str(e).splitlines()[0]}"
    try:
        value = json.loads(value)["password"]
    except (ValueError, KeyError, TypeError):
        pass
    s.values["DB_PASSWORD"], s.values["DB_SECRET_NAME"] = value, ""   # stages use DB_PASSWORD, no second lookup
    return None


def check_settings(s: Settings, stages: list) -> list:
    """Problems that would make a stage fail at once."""
    problems = []
    if not ENV_FILE.is_file():
        problems.append(f"{ENV_FILE} not found: cp .env.example .env and fill it in")
    missing = [pip for mod, pip in PACKAGES.items() if importlib.util.find_spec(mod) is None]
    if missing:
        problems.append(f"Python packages missing: {', '.join(missing)} (activate the venv: source .venv/bin/activate; "
                        "pip install -r requirements.txt)")
    if "bronze" in stages and not s.get("RAW_S3_ROOT"):
        problems.append("bronze: RAW_S3_ROOT is empty in .env")
    for stage in sorted(DB_STAGES & set(stages)):
        for key in ("DB_HOST", "DB_NAME", "DB_USER"):
            if not s.get(key):
                problems.append(f"{stage}: {key} is empty in .env")
    unknown = [x for x in s.stages if x not in ALL_STAGES]
    if unknown:
        problems.append(f"STAGES: unknown stage(s) {unknown} (known: {', '.join(ALL_STAGES)})")
    return sorted(set(problems))


def settings_warnings(s: Settings, stages: list) -> list:
    """Things that do not stop a run but are worth knowing at the start."""
    out = []
    if "bronze" in stages and shutil.which("tesseract") is None:
        out.append("bronze: the tesseract program is missing — images without GPS get no OCR position "
                   "(sudo apt install -y tesseract-ocr)")
    return out
