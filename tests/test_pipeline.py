"""Run rules, with every stage folder replaced by a fake (PIPELINE_CODE_ROOT): each fake records its call in
calls.txt and exits with the code of FAKE_<STAGE>_RC, so no AWS / database is needed."""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable


def fake_stages(tmp: Path) -> dict:
    calls = tmp / "calls.txt"
    sh = lambda name: f'echo "{name} $@" >> {calls}\nexit ${{FAKE_{name.upper()}_RC:-0}}\n'
    py = lambda name, rc: textwrap.dedent(f"""\
        import os, sys
        open({str(calls)!r}, "a").write("{name} " + " ".join(sys.argv[1:]) + "\\n")
        sys.exit(int({rc}))
        """)
    code = tmp / "code"
    for p in ("bronze", "silver", "images", "push_silver", "gold/src/ris_kpi", "push_gold"):
        (code / p).mkdir(parents=True)
    (code / "bronze/run_bronze_s3.sh").write_text(sh("bronze"))
    (code / "silver/run_silver_s3.sh").write_text(sh("silver"))
    (code / "images/upload_images.py").write_text(py("images", "os.getenv('FAKE_IMAGES_RC', 0)"))
    (code / "push_silver/push.py").write_text(py("push_silver", "os.getenv('FAKE_PUSH_WRITE_RC' if '--apply' in "
                                                                "sys.argv else 'FAKE_PUSH_CHECK_RC', 0)"))
    (code / "gold/src/ris_kpi/__init__.py").write_text("")
    (code / "gold/src/ris_kpi/__main__.py").write_text(py("gold", "os.getenv('FAKE_GOLD_RC', 0)"))
    (code / "push_gold/push_to_db.py").write_text(py("push_gold", "os.getenv('FAKE_PUSHGOLD_RC', 0)"))
    env = tmp / ".env"
    env.write_text(textwrap.dedent(f"""\
        RAW_S3_ROOT=s3://raw/root/
        DB_HOST=localhost
        DB_NAME=dhms_test
        DB_USER=tester
        DB_PASSWORD=pw
        SURVEYS_FILE={tmp / 'surveys.csv'}
        LOG_DIR={tmp / 'logs'}
        LOG_S3_URI=
        """))
    (tmp / "surveys.csv").write_text("zone,phase,cycle,upc\nB,1,1,N_02015_03002_GJ\nA,2,1,N/01002/01001/GJ\n")
    return {"env": env, "calls": calls, "code": code}


def run(tmp, *args, **rc):
    f = fake_stages(tmp)
    env = {**os.environ, "PIPELINE_ENV": str(f["env"]), "PIPELINE_CODE_ROOT": str(f["code"]),
           **{f"FAKE_{k.upper()}_RC": str(v) for k, v in rc.items()}}
    p = subprocess.run([PY, str(ROOT / "run.py"), *args], env=env, capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, cwd=tmp)
    calls = f["calls"].read_text().splitlines() if f["calls"].exists() else []
    return p, [c.split()[0] + (" write" if ("--apply" in c or "--dry-run false" in c) else "") for c in calls], p.stdout + p.stderr


def test_scheduled_all_ok_runs_every_stage_in_order(tmp_path):
    p, calls, out = run(tmp_path, "--scheduled")
    assert p.returncode == 0, out
    assert calls == ["bronze", "bronze", "silver", "silver", "images", "images",
                     "push_silver", "push_silver write", "push_silver", "push_silver write",
                     "gold", "gold", "push_gold", "push_gold write"]
    assert "Every step OK" in out


def test_stage_arguments(tmp_path):
    p, _, out = run(tmp_path, "--scheduled", "--only", "push_silver", "--db", "dhms_other")
    lines = (tmp_path / "calls.txt").read_text().splitlines()
    assert "--silver s3://dhms-new-stgae/DHMS/NSV/Silver/Zone=B/Phase=1/Cycle=1/UPC=N_02015_03002_GJ/dlm_kpi_new" in lines[0]
    assert "--db dhms_other" in lines[0] and lines[1].endswith("--apply")


def test_scheduled_stops_on_bronze_warning(tmp_path):
    p, calls, out = run(tmp_path, "--scheduled", bronze=2)
    assert p.returncode == 1 and calls == ["bronze"] and "scheduled runs stop on warnings" in out


def test_scheduled_stops_when_db_check_finds_blockers(tmp_path):
    p, calls, out = run(tmp_path, "--scheduled", push_check=1)
    assert p.returncode == 1 and "push_silver write" not in calls and "gold" not in calls


def test_failed_write_stops_the_run(tmp_path):
    p, calls, out = run(tmp_path, "--scheduled", push_write=1)
    assert p.returncode == 1 and calls[-1] == "push_silver write" and "gold" not in calls


def test_gold_failure_stops_before_its_push(tmp_path):
    p, calls, out = run(tmp_path, "--scheduled", gold=1)
    assert p.returncode == 1 and calls[-1] == "gold" and "push_gold" not in calls


def test_no_write_runs_checks_but_never_writes(tmp_path):
    p, calls, out = run(tmp_path, "--scheduled", "--no-write")
    assert p.returncode == 0
    assert not [c for c in calls if c.endswith("write")]
    assert calls.count("push_silver") == 2 and "gold" in calls and "push_gold" in calls


def test_manual_without_terminal_does_not_write(tmp_path):
    p, calls, out = run(tmp_path)
    assert p.returncode == 1 and "push_silver write" not in calls and "not confirmed" in out


def test_only_and_single_survey(tmp_path):
    p, calls, out = run(tmp_path, "--scheduled", "--only", "silver", "--zone", "B", "--phase", "1", "--cycle", "1",
                        "--upc", "N-02015-03002-GJ")
    assert p.returncode == 0 and calls == ["silver"] and "N/02015/03002/GJ zone B phase 1 cycle 1" in out


def test_missing_db_setting_is_a_settings_problem(tmp_path):
    f = fake_stages(tmp_path)
    f["env"].write_text(f["env"].read_text().replace("DB_HOST=localhost\n", ""))
    p = subprocess.run([PY, str(ROOT / "run.py"), "--scheduled"], capture_output=True, text=True, cwd=tmp_path,
                       env={**os.environ, "PIPELINE_ENV": str(f["env"]), "PIPELINE_CODE_ROOT": str(f["code"])})
    assert p.returncode == 2 and "push_silver: DB_HOST is empty" in p.stdout


def test_manual_answers(tmp_path, monkeypatch):
    """Manual run: a warning asks to continue; each database write asks for confirmation."""
    sys.path.insert(0, str(ROOT))
    from pipeline.runner import Run
    from pipeline.stages import Step
    answers = iter(["y", "n"])
    monkeypatch.setattr("builtins.input", lambda q: next(answers))
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    r = Run(tmp_path, scheduled=False, no_write=False, db_name="dhms_test")
    steps = [Step("silver", "silver x", [PY, "-c", "import sys; sys.exit(1)"], str(tmp_path), warn=(1,)),
             Step("push_silver", "push check", [PY, "-c", "pass"], str(tmp_path), kind="check"),
             Step("push_silver", "push write", [PY, "-c", "raise SystemExit('must not run')"], str(tmp_path), kind="write")]
    assert r.run_steps(steps) is False
    assert [(s.label, res) for s, res, *_ in r.results] == [("silver x", "WARNING"), ("push check", "OK"),
                                                             ("push write", "SKIPPED")]


def test_scheduled_stops_when_image_uploads_fail(tmp_path):
    p, calls, out = run(tmp_path, "--scheduled", images=1)
    assert p.returncode == 1 and calls[-1] == "images" and "push_silver" not in calls
