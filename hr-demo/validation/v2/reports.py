"""Local execution evidence; reruns never leave stale successful query CSVs."""
import csv
import hashlib
import json


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_results(directory, qid, outputs):
    for name, text in outputs.items():
        path = directory / f"{qid}.{name}.csv"
        path.unlink(missing_ok=True)
        if text is not None:
            path.write_text(text, encoding="utf-8")


def write_summary(directory, records, fields):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "summary.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)
