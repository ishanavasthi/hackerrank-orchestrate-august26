"""Output layer: writes output.csv from routing decisions.

Owned by worker C (M1). Depends only on code/contracts.py.
"""

from __future__ import annotations

import csv
from typing import Iterable, Mapping, Union

from contracts import (
    Decision,
    EVIDENCE_SEPARATOR,
    Message,
    NO_EVIDENCE,
    OUTPUT_COLUMNS,
)

DecisionsInput = Union[Mapping[str, Decision], Iterable[Decision]]


def write_output(
    decisions: DecisionsInput,
    out_path: str,
    messages: Iterable[Message],
) -> None:
    """Write output.csv.

    decisions: a dict of message_id -> Decision, or an iterable of Decision.
    out_path: destination path for the CSV.
    messages: dataset/messages.csv rows, in the order rows must be emitted.
              Every message.message_id must have a matching decision.
    """
    by_id = decisions if isinstance(decisions, Mapping) else {
        d.message_id: d for d in decisions
    }

    messages = list(messages)
    missing = [m.message_id for m in messages if m.message_id not in by_id]
    if missing:
        raise KeyError(f"write_output: no decision for message_id(s): {missing}")

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        csv_writer = csv.writer(f)
        csv_writer.writerow(OUTPUT_COLUMNS)
        for m in messages:
            csv_writer.writerow(_format_row(m.message_id, by_id[m.message_id]))


def _format_row(message_id: str, decision: Decision) -> list[str]:
    evidence = (
        EVIDENCE_SEPARATOR.join(decision.evidence_message_ids)
        if decision.evidence_message_ids
        else NO_EVIDENCE
    )
    return [
        message_id,
        decision.action,
        decision.message_type,
        _sanitize_reason(decision.reason),
        f"{float(decision.confidence):.2f}",
        evidence,
    ]


def _sanitize_reason(reason: str) -> str:
    return reason.replace("\r\n", " ").replace("\n", " ").replace("\r", " ").strip()
