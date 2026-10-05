#!/usr/bin/env python3
"""Fixed SQL A/B regression; subsets write runs/<ids>/ instead of full reports."""
import argparse
from pathlib import Path
import sys

from questions import QUESTIONS
from query_execution import (load_env, positive_timeout, regression_questions,
                             regression_directory, run_regression)

HERE = Path(__file__).resolve().parent


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", nargs="+")
    parser.add_argument("--domain")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--timeout", type=positive_timeout, default=180)
    args = parser.parse_args(argv)
    try:
        questions = regression_questions(QUESTIONS, args.only, args.domain)
        directory = regression_directory(HERE, questions, args.only, args.domain, args.output_dir)
        env = load_env()
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    return run_regression(questions, directory, env=env, timeout=args.timeout, execution_evidence=True)


if __name__ == "__main__":
    sys.exit(main())
