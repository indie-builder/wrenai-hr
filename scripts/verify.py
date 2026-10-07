#!/usr/bin/env python3
"""Run explicit verification suites locally and in CI without replacing the development DB."""
import argparse
import json
import os
from pathlib import Path
import zipfile
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
ANALYSIS = ROOT / ".venv/bin/python"
SERVICE = ROOT / ".venv-mcp/bin/python"


def run(command, *, env=None, cwd=ROOT):
    print("+ " + " ".join(map(str, command)), flush=True)
    subprocess.run(list(map(str, command)), cwd=cwd, env=env, check=True)


def publish_question_package(package, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as folder:
        archive = Path(folder) / "package.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
            for path in sorted(package.rglob("*")):
                if path.is_file():
                    output.write(path, path.relative_to(package))
        archive.replace(destination)


def data_checks(fresh):
    if not fresh:
        run([ANALYSIS, "hr-demo/validation/v2/run_all.py", "--output-dir", ".ci/regression"])
        run([ANALYSIS, "hr-demo/scripts/export_dashboard.py", "--check"])
        return
    with tempfile.TemporaryDirectory(prefix="hr-verify-") as folder:
        temporary = Path(folder)
        database = temporary / "db/public.duckdb"
        env = {**os.environ, "WREN_HOME": str(temporary / "wren")}
        run([ANALYSIS, "-c", "import runpy,sys; runpy.run_path('hr-demo/db/build_duckdb.py')['build'](sys.argv[1])", database])
        profile = temporary / "profile.json"
        profile.write_text(json.dumps({"datasource": "duckdb", "properties": {
            "url": str(database.parent), "format": "duckdb"}}))
        wren = ROOT / ".venv/bin/wren"
        run([wren, "profile", "add", "hr_demo_duck", "--from-file", profile], env=env)
        for command in ("validate", "build"):
            run([wren, "context", command], cwd=ROOT / "hr-demo/wren-project", env=env)
        run([ANALYSIS, "hr-demo/validation/v2/run_all.py", "--database", database,
             "--output-dir", ".ci/regression"], env=env)
        for flags in ([], ["--check"]):
            run([ANALYSIS, "hr-demo/scripts/export_dashboard.py", "--database", database, *flags], env=env)
        with tempfile.TemporaryDirectory(prefix="hr-nl-") as nl:
            package = Path(nl) / "package"
            run([ANALYSIS, "hr-demo/validation/v2/eval/nl_eval.py", "export", "--output-dir", package], env=env)
            publish_question_package(package, ROOT / ".ci/nl-package.zip")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("suites", nargs="+", choices=("docs", "analysis", "mcp", "data", "installation"))
    parser.add_argument("--fresh-data", action="store_true", help="构建临时库和profile，导出可重建页面产物")
    args = parser.parse_args()
    suites = {
        "docs": [[sys.executable, "-m", "unittest", "discover", "-s", "scripts/tests", "-p", "test_check_docs.py", "-v"],
                 [sys.executable, "scripts/check_docs.py"], [sys.executable, "scripts/check_architecture.py"]],
        "analysis": [["uv", "lock", "--check"],
                     [ANALYSIS, "-m", "unittest", "discover", "-s", "hr-demo/validation/v2/tests", "-v"],
                     [ANALYSIS, "-m", "unittest", "discover", "-s", "scripts/tests", "-p", "test_skill_scripts.py", "-v"],
                     [ANALYSIS, "-m", "unittest", "discover", "-s", "scripts/tests", "-p", "test_architecture.py", "-v"],
                     [ANALYSIS, "hr-demo/validation/v2/check_semantics.py", "--build-check"]],
        "mcp": [[SERVICE, "-m", "scripts.prepare_mcp"],
                [SERVICE, "-m", "unittest", "discover", "-s", "tests", "-v"],
                [SERVICE, "scripts/smoke_mcp.py"]],
        "installation": [[SERVICE, "-m", "scripts.prepare_mcp"], [sys.executable, "scripts/check_installation.py"]],
    }
    try:
        for suite in args.suites:
            if suite == "data":
                data_checks(args.fresh_data)
            else:
                for command in suites[suite]:
                    run(command)
    except subprocess.CalledProcessError as error:
        return error.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
