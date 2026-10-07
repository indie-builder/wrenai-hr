#!/usr/bin/env python3
"""Export the standalone analytics runner from canonical package sources."""
import argparse
from pathlib import Path
import shutil
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def export_runner(destination):
    destination = Path(destination).resolve()
    if destination.exists():
        raise ValueError("输出目录必须不存在")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
        staged = Path(temporary) / "runner"
        staged.mkdir()
        for package in ("hr_contracts", "hr_analytics"):
            shutil.copytree(ROOT / "src" / package, staged / package,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        (staged / "hr_query").mkdir()
        for name in ("__init__.py", "duckdb_worker.py"):
            shutil.copyfile(ROOT / "src/hr_query" / name, staged / "hr_query" / name)
        shutil.copyfile(ROOT / ".agents/skills/semantic-analytics/scripts/run_all.py", staged / "run_all.py")
        staged.rename(destination)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    print(export_runner(args.destination))


if __name__ == "__main__":
    main()
