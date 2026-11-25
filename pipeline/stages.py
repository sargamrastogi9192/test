"""What each stage runs (all in this repository), and what its exit code means.

  stage        per      command                                                   exit codes
  bronze       survey   bronze/run_bronze_s3.sh                                   0 ok, 2 warning (sheet rejected)
  silver       survey   silver/run_silver_s3.sh                                   0 ok, 1 warning (table / DQ check FAILED)
  images       survey   images/upload_images.py (breach images → CDN, urls into silver) 0 ok, 1 warning (uploads failed)
  push_silver  survey   push_silver/push.py --silver … --db …          (check)    0 ok, else blockers
                        push_silver/push.py … --apply                  (write)    0 ok, else rolled back
  gold         run      python -m ris_kpi run --upc-list …, then sync-db-ids     0 ok
  push_gold    run      push_gold/push_to_db.py --dry-run true         (check)    0 ok, else rejected values
                        push_gold/push_to_db.py --dry-run false        (write)    0 ok, else rolled back

Database stages run a check first; the write runs only after a clean check.
"""

import sys
from dataclasses import dataclass, field

from .settings import CODE, Settings
from .surveys import Survey


@dataclass
class Step:
    stage: str
    label: str
    cmd: list
    cwd: str
    env: dict = field(default_factory=dict)
    survey: Survey | None = None
    ok: tuple = (0,)
    warn: tuple = ()
    kind: str = "run"          # run | check | write  (write = changes the database)


def survey_args(v: Survey) -> list:
    return ["--zone", v.zone, "--phase", v.phase, "--cycle", v.cycle, "--upc", v.upc]


def steps_for(stage: str, s: Settings, surveys: list, gold_list) -> list:
    env, py, root = s.stage_env(), sys.executable, str(CODE)
    if stage == "bronze":
        return [Step(stage, f"bronze {v}", ["bash", str(CODE / "bronze/run_bronze_s3.sh"), *survey_args(v),
                                             "--raw-root", s.get("RAW_S3_ROOT")], root, env, v, warn=(2,))
                for v in surveys]
    if stage == "silver":
        # no tracker: survey dates come from D1 / D4 and the CA / IRC standard from D2 (silver/common/project.py)
        extra = ["--master-project", s.get("MASTER_PROJECT_S3")] if s.get("MASTER_PROJECT_S3") else []
        return [Step(stage, f"silver {v}", ["bash", str(CODE / "silver/run_silver_s3.sh"), *survey_args(v),
                                             "--bucket", s.get("BUCKET", "dhms-new-stgae"), *extra], root, env, v, warn=(1,))
                for v in surveys]
    if stage == "images":
        return [Step(stage, f"images {v}", [py, str(CODE / "images/upload_images.py"), *survey_args(v)], root, env, v,
                     warn=(1,)) for v in surveys]
    if stage == "push_silver":
        out, cwd = [], str(CODE / "push_silver")
        for v in surveys:
            cmd = [py, "push.py", "--silver", f"{s.silver_s3}/{v.folder}/dlm_kpi_new", "--db", s.db_name]
            out.append(Step(stage, f"push_silver check {v}", cmd, cwd, env, v, kind="check"))
            out.append(Step(stage, f"push_silver write {v}", cmd + ["--apply"], cwd, env, v, kind="write"))
        return out
    if stage == "gold":
        return [Step(stage, "gold run", [py, "-m", "ris_kpi", "run", "--upc-list", str(gold_list)], root, env),
                Step(stage, "gold sync-db-ids", [py, "-m", "ris_kpi", "sync-db-ids"], root, env)]
    if stage == "push_gold":
        cmd = [py, str(CODE / "push_gold/push_to_db.py"), "--dry-run"]
        return [Step(stage, "push_gold check", cmd + ["true"], root, env, kind="check"),
                Step(stage, "push_gold write", cmd + ["false"], root, env, kind="write")]
    raise ValueError(stage)
