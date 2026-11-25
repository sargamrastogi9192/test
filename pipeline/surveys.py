"""The surveys of a run: surveys.csv (zone, phase, cycle, upc) or one survey from the command line."""

import csv
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Survey:
    zone: str
    phase: str
    cycle: str
    upc: str            # N/01002/01001/GJ

    @property
    def upc_code(self) -> str:          # N_01002_01001_GJ (folder names)
        return self.upc.replace("/", "_")

    @property
    def folder(self) -> str:            # Zone=B/Phase=1/Cycle=1/UPC=N_01002_01001_GJ
        return f"Zone={self.zone}/Phase={self.phase}/Cycle={self.cycle}/UPC={self.upc_code}"

    def __str__(self):
        return f"{self.upc} zone {self.zone} phase {self.phase} cycle {self.cycle}"


def make_survey(zone, phase, cycle, upc) -> Survey:
    clean = lambda x: str(x or "").strip()
    number = lambda x: re.sub(r"\.0$", "", clean(x))
    upc = re.sub(r"[-_\s]+", "/", clean(upc)).upper()
    s = Survey(clean(zone).upper(), number(phase), number(cycle), upc)
    if not all([s.zone, s.phase, s.cycle, s.upc]):
        raise ValueError(f"incomplete survey: zone={zone!r} phase={phase!r} cycle={cycle!r} upc={upc!r}")
    return s


def read_surveys(path: Path) -> list:
    if not path.is_file():
        raise SystemExit(f"Survey list {path} not found (columns: zone, phase, cycle, upc)")
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    out = []
    for n, row in enumerate(rows, start=2):
        row = {str(k).strip().lower(): v for k, v in row.items() if k}
        if not any((v or "").strip() for v in row.values()):
            continue
        try:
            s = make_survey(row.get("zone"), row.get("phase"), row.get("cycle"), row.get("upc"))
        except ValueError as e:
            raise SystemExit(f"{path.name} line {n}: {e}")
        if s not in out:
            out.append(s)
    if not out:
        raise SystemExit(f"{path} lists no survey")
    return out


def write_gold_list(surveys: list, path: Path) -> Path:
    """The survey list in the form the gold repo reads (upc, phase, cycle, zone)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["upc", "phase", "cycle", "zone"])
        for s in surveys:
            w.writerow([s.upc, s.phase, s.cycle, s.zone])
    return path
