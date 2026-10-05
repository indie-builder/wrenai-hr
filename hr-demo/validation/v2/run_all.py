#!/usr/bin/env python3
"""Fixed SQL A/B regression; subsets write runs/<ids>/ instead of full reports."""
import argparse
import hashlib
from pathlib import Path
import sys

from questions import QUESTIONS
from query_execution import (evaluate, load_env, positive_timeout, run_gt, run_wren,
                             write_json, write_results, write_summary)

HERE = Path(__file__).resolve().parent


def select_questions(questions, only=None, domain=None):
    if only is not None:
        unknown = set(only) - {question["id"] for question in questions}
        if unknown:
            raise ValueError("未知题号: " + ", ".join(sorted(unknown)))
    selected = [question for question in questions if (only is None or question["id"] in only)
                and (domain is None or question["domain"] == domain)]
    if not selected:
        raise ValueError("没有匹配的题目")
    return selected


def output_directory(questions, *, subset=False, output=None):
    if output:
        directory = Path(output).resolve()
        if subset and directory == HERE.resolve():
            raise ValueError("局部运行不能写入全量报告目录；请使用独立--output-dir")
        return directory
    if not subset:
        return HERE
    ids = "-".join(question["id"] for question in questions)
    key = ids if len(ids) < 80 else hashlib.sha256(ids.encode()).hexdigest()[:16]
    return HERE / "runs" / key


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", nargs="+")
    parser.add_argument("--domain")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--timeout", type=positive_timeout, default=180)
    args = parser.parse_args(argv)
    try:
        questions = select_questions(QUESTIONS, args.only, args.domain)
        directory = output_directory(questions, subset=args.only is not None or args.domain is not None,
                                     output=args.output_dir)
        env = load_env()
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    results = directory / "results"
    results.mkdir(parents=True, exist_ok=True)
    records = []
    for question in questions:
        qid = question["id"]
        gt = run_gt(question["gt"], timeout=args.timeout)
        wren = run_wren(question["wren"], env=env, timeout=args.timeout)
        write_results(results, qid, {"gt": gt.stdout if gt.ok else None, "wren": wren.stdout if wren.ok else None})
        ok, message = evaluate(question, gt, wren)
        record = {**{key: question[key] for key in ("id", "domain", "priority", "question")},
                  "result": "PASS" if ok else "FAIL", "msg": message}
        records.append(record)
        write_json(results / f"{qid}.execution.json", {"gt": gt.trace(), "wren": wren.trace()})
        print(f"{record['result']} {qid} [{question['domain']}] {message}")
    write_summary(directory, records, ["id", "domain", "priority", "question", "result", "msg"])
    passed = sum(record["result"] == "PASS" for record in records)
    print(f"{passed}/{len(records)} PASS；报告: {directory / 'summary.csv'}")
    return 0 if passed == len(records) else 1


if __name__ == "__main__":
    sys.exit(main())
