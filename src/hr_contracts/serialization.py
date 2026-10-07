"""Canonical JSON and file fingerprints shared by artifact producers."""
import hashlib
import json


def canonical(value):
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def write_json(path, value):
    path.write_text(canonical(value), encoding="utf-8")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
