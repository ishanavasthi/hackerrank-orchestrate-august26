"""Standalone checker for output.csv.

Usage: python code/validate.py output.csv

Runnable in isolation, the way a grader would run it: reads the CSV from
disk, imports nothing from this project except code/contracts.py, and never
imports writer.py or main.py.

Exits 0 on pass, 1 on failure. Prints every failure found, not just the
first, then a one-line summary.
"""

from __future__ import annotations

import csv
import os
import sys
from collections import Counter

from contracts import (
    ACTIONS,
    EVIDENCE_SEPARATOR,
    MESSAGE_TYPES,
    NO_EVIDENCE,
    OUTPUT_COLUMNS,
)

DATASET_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dataset")


def _read_csv_rows(path: str) -> tuple[list[str], list[list[str]]]:
    """Return (header, data_rows) as raw string lists, RFC4180-aware."""
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        all_rows = list(reader)
    if not all_rows:
        return [], []
    return all_rows[0], all_rows[1:]


def _load_message_ids(path: str) -> list[str]:
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return [row["message_id"] for row in reader]


def validate(output_path: str) -> list[tuple[str, str]]:
    """Return a list of (category, description) problems. Empty == pass."""
    problems: list[tuple[str, str]] = []

    if not os.path.isfile(output_path):
        return [("missing_file", f"output file not found: {output_path}")]

    header, data_rows = _read_csv_rows(output_path)

    if tuple(header) != OUTPUT_COLUMNS:
        problems.append((
            "header",
            f"expected header {list(OUTPUT_COLUMNS)}, got {header}",
        ))

    col_count = len(OUTPUT_COLUMNS)
    row_dicts: list[tuple[int, dict[str, str]]] = []
    for idx, row in enumerate(data_rows, start=1):
        if len(row) != col_count:
            problems.append((
                "row_shape",
                f"row {idx}: expected {col_count} columns, got {len(row)}: {row}",
            ))
            continue
        row_dicts.append((idx, dict(zip(OUTPUT_COLUMNS, row))))

    messages_path = os.path.join(DATASET_DIR, "messages.csv")
    expected_ids = _load_message_ids(messages_path)
    expected_id_set = set(expected_ids)
    expected_count = len(expected_ids)

    if len(data_rows) != expected_count:
        problems.append((
            "row_count",
            f"expected {expected_count} rows (from dataset/messages.csv), got {len(data_rows)}",
        ))

    output_ids = [r["message_id"] for _, r in row_dicts]
    output_id_counts = Counter(output_ids)
    for msg_id, count in output_id_counts.items():
        if count > 1:
            problems.append((
                "duplicate_id",
                f"message_id {msg_id!r} appears {count} times",
            ))

    output_id_set = set(output_ids)
    missing_ids = expected_id_set - output_id_set
    if missing_ids:
        problems.append((
            "missing_id",
            f"{len(missing_ids)} message_id(s) from messages.csv missing in output: "
            f"{sorted(missing_ids)}",
        ))
    extra_ids = output_id_set - expected_id_set
    if extra_ids:
        problems.append((
            "extra_id",
            f"{len(extra_ids)} message_id(s) in output not present in messages.csv: "
            f"{sorted(extra_ids)}",
        ))

    history_path = os.path.join(DATASET_DIR, "message_history.csv")
    history_ids = set(_load_message_ids(history_path))

    for idx, row in row_dicts:
        msg_id = row["message_id"]

        action = row["action"]
        if action not in ACTIONS:
            problems.append((
                "invalid_action",
                f"row {idx} ({msg_id}): action {action!r} not in {sorted(ACTIONS)}",
            ))

        message_type = row["message_type"]
        if message_type not in MESSAGE_TYPES:
            problems.append((
                "invalid_message_type",
                f"row {idx} ({msg_id}): message_type {message_type!r} not in {sorted(MESSAGE_TYPES)}",
            ))

        reason = row["reason"]
        if not reason.strip():
            problems.append((
                "invalid_reason",
                f"row {idx} ({msg_id}): reason is empty",
            ))
        elif "\n" in reason or "\r" in reason:
            problems.append((
                "invalid_reason",
                f"row {idx} ({msg_id}): reason contains a newline character",
            ))

        confidence_raw = row["confidence"]
        try:
            confidence = float(confidence_raw)
        except ValueError:
            problems.append((
                "invalid_confidence",
                f"row {idx} ({msg_id}): confidence {confidence_raw!r} is not a float",
            ))
        else:
            if not (0.0 <= confidence <= 1.0):
                problems.append((
                    "invalid_confidence",
                    f"row {idx} ({msg_id}): confidence {confidence} out of range [0.0, 1.0]",
                ))

        evidence_raw = row["evidence_message_ids"]
        if not evidence_raw:
            problems.append((
                "invalid_evidence",
                f"row {idx} ({msg_id}): evidence_message_ids is empty "
                f"(use {NO_EVIDENCE!r} for no evidence)",
            ))
        elif evidence_raw != NO_EVIDENCE:
            evidence_ids = evidence_raw.split(EVIDENCE_SEPARATOR)
            for ev_id in evidence_ids:
                if not ev_id:
                    problems.append((
                        "invalid_evidence",
                        f"row {idx} ({msg_id}): evidence_message_ids {evidence_raw!r} "
                        f"has an empty id",
                    ))
                elif ev_id not in history_ids:
                    problems.append((
                        "dangling_evidence",
                        f"row {idx} ({msg_id}): evidence id {ev_id!r} not found in "
                        f"dataset/message_history.csv",
                    ))

    return problems


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python code/validate.py <output.csv>")
        return 2

    output_path = argv[1]
    problems = validate(output_path)

    if not problems:
        header, data_rows = _read_csv_rows(output_path)
        print(f"PASS {len(data_rows)} rows")
        return 0

    for category, description in problems:
        print(f"FAIL [{category}] {description}")

    counts = Counter(category for category, _ in problems)
    counts_str = ", ".join(f"{cat}={n}" for cat, n in sorted(counts.items()))
    print(f"FAIL {len(problems)} problems ({counts_str})")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
