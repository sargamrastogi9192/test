"""Runs the steps in order and decides after each one: go on, ask, or stop.

  result of a step     scheduled (--scheduled)          manual (default)
  OK                   go on                            go on
  WARNING              stop                             ask "continue?"
  FAILED               stop                             stop
  DB write             only after a clean check         show the check, ask "write to <db>?"
  --no-write           every write is skipped (checks still run)

Every line of every stage goes to the console and to logs/<run>/pipeline.log; summary.csv lists each step.
"""

import csv
import fcntl
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from .stages import Step


class Run:
    def __init__(self, log_dir: Path, scheduled: bool, no_write: bool, db_name: str):
        self.started = datetime.now()
        self.dir = log_dir / f"run_{self.started:%Y%m%d_%H%M%S}"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.log = open(self.dir / "pipeline.log", "a", encoding="utf-8")
        self.scheduled, self.no_write, self.db_name = scheduled, no_write, db_name
        self.results = []                 # (step, result, seconds, exit code, note)
        self.stopped = None               # reason the run stopped early

    # ── output ────────────────────────────────────────────────────────────────
    def say(self, text=""):
        print(text, flush=True)
        self.log.write(text + "\n")
        self.log.flush()

    def ask(self, question: str) -> bool:
        if self.scheduled:
            return False
        if not sys.stdin.isatty():
            self.say(f"{question} -> no terminal to answer: treated as NO (use --scheduled for unattended runs)")
            return False
        answer = input(f"{question} [y/N] ").strip().lower()
        self.log.write(f"{question} [y/N] {answer}\n")
        return answer in ("y", "yes")

    # ── one step ──────────────────────────────────────────────────────────────
    def execute(self, step: Step) -> tuple:
        self.say(f"\n{'=' * 100}\n{step.label}\n  $ {' '.join(step.cmd)}\n  (in {step.cwd})\n{'=' * 100}")
        t0 = time.time()
        try:
            proc = subprocess.Popen(step.cmd, cwd=step.cwd, env={**os.environ, **step.env},
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
            for line in proc.stdout:
                self.say(line.rstrip("\n"))
            rc = proc.wait()
        except (FileNotFoundError, PermissionError, NotADirectoryError) as e:
            self.say(f"Could not start: {e}")
            rc = 127
        result = "OK" if rc in step.ok else "WARNING" if rc in step.warn else "FAILED"
        return result, round(time.time() - t0, 1), rc

    def record(self, step, result, seconds=0.0, rc=None, note=""):
        self.results.append((step, result, seconds, rc, note))
        self.say(f"--> {step.label}: {result}" + (f" (exit {rc})" if rc is not None else "") + (f" — {note}" if note else ""))

    def stop(self, reason: str):
        self.stopped = reason
        self.say(f"\n!! STOPPED: {reason}")

    # ── the stages ────────────────────────────────────────────────────────────
    def run_steps(self, steps: list) -> bool:
        """Run the steps of one stage. False = the run must stop."""
        check_ok = {}
        for step in steps:
            key = step.survey
            if step.kind == "write":
                if not check_ok.get(key):
                    continue                                   # its check did not pass (already stopped)
                if self.no_write:
                    self.record(step, "SKIPPED", note="--no-write")
                    continue
                if not self.scheduled and not self.ask(f"Check passed. Write {step.stage} to database '{self.db_name}'"
                                                       + (f" for {step.survey}" if step.survey else "") + "?"):
                    self.record(step, "SKIPPED", note="not confirmed")
                    self.stop(f"{step.label} not confirmed — nothing written; re-run with --from {step.stage}")
                    return False
            result, seconds, rc = self.execute(step)
            self.record(step, result, seconds, rc)
            if step.kind == "check":
                check_ok[key] = result == "OK"
            if result == "FAILED":
                why = "check found blockers (see its report)" if step.kind == "check" else f"exit code {rc}"
                self.stop(f"{step.label}: {why}")
                return False
            if result == "WARNING":
                if self.scheduled:
                    self.stop(f"{step.label}: warning (exit {rc}) — scheduled runs stop on warnings")
                    return False
                if not self.ask(f"{step.label} finished with a warning (exit {rc}). Continue?"):
                    self.stop(f"{step.label}: warning, not continued")
                    return False
        return True

    # ── end ───────────────────────────────────────────────────────────────────
    def summary(self) -> int:
        self.say(f"\n{'#' * 40} summary {'#' * 40}")
        for step, result, seconds, rc, note in self.results:
            self.say(f"  {result:8s} {seconds:8.1f}s  {step.label}" + (f"  ({note})" if note else ""))
        self.say(f"  {'STOPPED: ' + self.stopped if self.stopped else 'Every step OK'}")
        self.say(f"  log: {self.dir / 'pipeline.log'}")
        with open(self.dir / "summary.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["stage", "step", "survey", "result", "seconds", "exit_code", "note"])
            for step, result, seconds, rc, note in self.results:
                w.writerow([step.stage, step.label, str(step.survey or ""), result, seconds, rc, note])
        self.log.close()
        return 1 if self.stopped else 0


class Lock:
    """One run at a time (a scheduled run must not overlap a manual one)."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.f = open(path, "w")

    def __enter__(self):
        try:
            fcntl.flock(self.f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("Another pipeline run is in progress (logs/.lock) — not started.")
        return self

    def __exit__(self, *exc):
        fcntl.flock(self.f, fcntl.LOCK_UN)
        self.f.close()
