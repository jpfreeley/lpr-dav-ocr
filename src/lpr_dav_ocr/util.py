"""Small file helpers. They close their files, which matters in the long-running watcher."""
import csv
import json
from pathlib import Path


def read_csv(path):
    with Path(path).open(newline="") as f:
        return list(csv.DictReader(f))


def read_jsonl(path):
    with Path(path).open() as f:
        return [json.loads(line) for line in f if line.strip()]
