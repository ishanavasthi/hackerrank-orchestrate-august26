"""Loads and indexes dataset/*.csv into Message rows and MessageContext joins."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Optional, Union

try:
    from code.contracts import MediaExtract, Message, MessageContext
    from code.media_cache import load_media_cache
except ImportError:
    from contracts import MediaExtract, Message, MessageContext
    from media_cache import load_media_cache


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8", newline="") as f:
        return [dict(row) for row in csv.DictReader(f)]


def _to_int(value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _row_to_message(row: dict[str, str]) -> Message:
    return Message(
        message_id=row.get("message_id") or "",
        user_id=row.get("user_id") or "",
        conversation_type=row.get("conversation_type") or "",
        group_id=row.get("group_id") or "",
        business_id=row.get("business_id") or "",
        sender_user_id=row.get("sender_user_id") or "",
        created_at=row.get("created_at") or "",
        message_text=row.get("message_text") or "",
        media_type=row.get("media_type") or "",
        media_id=row.get("media_id") or "",
        forwarded_count=_to_int(row.get("forwarded_count") or ""),
    )


class Dataset:
    """All dataset CSVs, loaded once and indexed for per-message context lookups."""

    def __init__(
        self,
        messages: list[Message],
        sample_messages: list[dict[str, str]],
        users_by_id: dict[str, dict[str, str]],
        groups_by_id: dict[str, dict[str, str]],
        membership_by_key: dict[tuple[str, str], dict[str, str]],
        business_by_id: dict[str, dict[str, str]],
        business_history_by_key: dict[tuple[str, str], dict[str, str]],
        history_by_user: dict[str, list[dict[str, str]]],
        events_by_message_id: dict[str, dict[str, str]],
        notification_load_by_user: dict[str, list[dict[str, str]]],
        media_cache: dict[str, MediaExtract],
    ) -> None:
        self.messages = messages
        self.sample_messages = sample_messages
        self._users_by_id = users_by_id
        self._groups_by_id = groups_by_id
        self._membership_by_key = membership_by_key
        self._business_by_id = business_by_id
        self._business_history_by_key = business_history_by_key
        self._history_by_user = history_by_user
        self._events_by_message_id = events_by_message_id
        self._notification_load_by_user = notification_load_by_user
        self._media_cache = media_cache

    @classmethod
    def load(cls, dataset_dir: Union[str, Path], media_cache_path: Union[str, Path]) -> "Dataset":
        dataset_dir = Path(dataset_dir)

        messages = [_row_to_message(row) for row in _read_csv(dataset_dir / "messages.csv")]
        sample_messages = _read_csv(dataset_dir / "sample_messages.csv")

        users_by_id = {
            row["user_id"]: row for row in _read_csv(dataset_dir / "users.csv") if row.get("user_id")
        }
        groups_by_id = {
            row["group_id"]: row for row in _read_csv(dataset_dir / "groups.csv") if row.get("group_id")
        }
        business_by_id = {
            row["business_id"]: row
            for row in _read_csv(dataset_dir / "business_accounts.csv")
            if row.get("business_id")
        }

        membership_by_key: dict[tuple[str, str], dict[str, str]] = {}
        for row in _read_csv(dataset_dir / "group_members.csv"):
            membership_by_key[(row.get("group_id", ""), row.get("user_id", ""))] = row

        business_history_by_key: dict[tuple[str, str], dict[str, str]] = {}
        for row in _read_csv(dataset_dir / "user_business_history.csv"):
            business_history_by_key[(row.get("user_id", ""), row.get("business_id", ""))] = row

        history_by_user: dict[str, list[dict[str, str]]] = {}
        for row in _read_csv(dataset_dir / "message_history.csv"):
            history_by_user.setdefault(row.get("user_id", ""), []).append(row)

        events_by_message_id = {
            row["message_id"]: row
            for row in _read_csv(dataset_dir / "message_events.csv")
            if row.get("message_id")
        }

        notification_load_by_user: dict[str, list[dict[str, str]]] = {}
        for row in _read_csv(dataset_dir / "daily_notification_summary.csv"):
            notification_load_by_user.setdefault(row.get("user_id", ""), []).append(row)

        media_cache = load_media_cache(media_cache_path)

        return cls(
            messages=messages,
            sample_messages=sample_messages,
            users_by_id=users_by_id,
            groups_by_id=groups_by_id,
            membership_by_key=membership_by_key,
            business_by_id=business_by_id,
            business_history_by_key=business_history_by_key,
            history_by_user=history_by_user,
            events_by_message_id=events_by_message_id,
            notification_load_by_user=notification_load_by_user,
            media_cache=media_cache,
        )

    def _media_for(self, message: Message) -> Optional[MediaExtract]:
        if not message.media_id:
            return None
        cached = self._media_cache.get(message.media_id)
        if cached is not None:
            return cached
        return MediaExtract(media_id=message.media_id, text="", available=False)

    def context_for(self, message: Message) -> MessageContext:
        history = self._history_by_user.get(message.user_id, [])

        events: dict[str, dict[str, str]] = {}
        for row in history:
            mid = row.get("message_id", "")
            if not mid:
                continue
            event = self._events_by_message_id.get(mid)
            if event is not None:
                events[mid] = event

        return MessageContext(
            message=message,
            media=self._media_for(message),
            user=self._users_by_id.get(message.user_id, {}),
            group=self._groups_by_id.get(message.group_id, {}),
            membership=self._membership_by_key.get((message.group_id, message.user_id), {}),
            business=self._business_by_id.get(message.business_id, {}),
            business_history=self._business_history_by_key.get(
                (message.user_id, message.business_id), {}
            ),
            history=history,
            events=events,
            notification_load=self._notification_load_by_user.get(message.user_id, []),
        )
