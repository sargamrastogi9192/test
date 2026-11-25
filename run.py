"""NSV pipeline (new-format surveys): raw S3 → bronze → silver → DLM tables (DB) → gold → RIS KPI tables (DB).

    python run.py                                   # manual: surveys.csv, asks before each database write
    python run.py --scheduled                       # unattended (cron): stops on any warning or error
    python run.py --zone B --phase 1 --cycle 1 --upc N/02015/03002/GJ
    python run.py --from gold                       # start at a stage (also --to, --only)
    python run.py --no-write                        # everything, database checks too, but never write
    python run.py --db dhms_uat                     # database of both pushes (else DB_NAME in .env)

Settings: .env. Exit code: 0 every step OK, 1 stopped (error / warning / write not confirmed), 2 settings problem.
"""

import argparse
import subprocess
import sys
from pathlib import Path

from pipeline.runner import Lock, Run
from pipeline.settings import (ALL_STAGES, DB_STAGES, ENV_FILE, check_settings, load_settings, resolve_db_password,
                               settings_warnings)
from pipeline.stages import steps_for
from pipeline.surveys import make_survey, read_surveys, write_gold_list


def parse_args():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter,
                                epilog="\n".join(__doc__.split("\n")[2:]))
    p.add_argument("--scheduled", action="store_true", help="unattended: no questions, stop on any warning or error")
    p.add_argument("--no-write", action="store_true", help="never write to the database (checks still run)")
    p.add_argument("--db", help="database name (overrides DB_NAME in .env)")
    p.add_argument("--surveys", help="survey list CSV: zone, phase, cycle, upc (default SURVEYS_FILE)")
    for k in ("zone", "phase", "cycle", "upc"):
        p.add_argument(f"--{k}", help=f"one survey: {k} (all four needed)")
    p.add_argument("--from", dest="start", choices=ALL_STAGES, help="first stage")
    p.add_argument("--to", dest="end", choices=ALL_STAGES, help="last stage")
    p.add_argument("--only", choices=ALL_STAGES, help="just this stage")
    return p.parse_args()


def main() -> int:
    a = parse_args()
    s = load_settings(a.db)

    one = [a.zone, a.phase, a.cycle, a.upc]
    if any(one) and not all(one):
        print("--zone, --phase, --cycle and --upc go together")
        return 2
    surveys = [make_survey(*one)] if all(one) else read_surveys(Path(a.surveys) if a.surveys
                                                                 else s.path("SURVEYS_FILE", "surveys.csv"))
    start, end = (a.only, a.only) if a.only else (a.start or ALL_STAGES[0], a.end or ALL_STAGES[-1])
    stages = [x for x in ALL_STAGES[ALL_STAGES.index(start):ALL_STAGES.index(end) + 1] if x in s.stages]
    if not stages:
        print(f"No stage to run (STAGES in {ENV_FILE.name}: {', '.join(s.stages)})")
        return 2
    problems = check_settings(s, stages)
    if not problems and DB_STAGES & set(stages):
        problem = resolve_db_password(s)
        problems += [f"database password: {problem}"] if problem else []
    if problems:
        print("Settings problems:\n  " + "\n  ".join(problems))
        return 2

    with Lock(s.path("LOG_DIR", "logs") / ".lock"):
        run = Run(s.path("LOG_DIR", "logs"), a.scheduled, a.no_write, s.db_name)
        gold_list = write_gold_list(surveys, run.dir / "gold_surveys.csv")
        run.say(f"NSV pipeline {run.started:%Y-%m-%d %H:%M:%S} | {'SCHEDULED' if a.scheduled else 'MANUAL'}"
                f"{' | NO WRITE' if a.no_write else ''} | database {s.db_name or '-'}")
        run.say(f"Stages:  {' → '.join(stages)}")
        run.say("Surveys: " + "; ".join(str(v) for v in surveys))
        for w in settings_warnings(s, stages):
            run.say(f"WARNING: {w}")

        for stage in stages:
            if not run.run_steps(steps_for(stage, s, surveys, gold_list)):
                break
        code = run.summary()

    log_s3 = s.get("LOG_S3_URI").rstrip("/")
    if log_s3:                                        # copy the run's log folder to S3 (failure only warns)
        dest = f"{log_s3}/{run.dir.name}/"
        rc = subprocess.call(["aws", "s3", "cp", str(run.dir), dest, "--recursive", "--only-show-errors"])
        print(f"log copied to {dest}" if rc == 0 else f"WARNING: log not copied to {dest}")
    return code


if __name__ == "__main__":
    sys.exit(main())
