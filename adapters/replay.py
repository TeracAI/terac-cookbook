"""Controlled synthetic events; inspection never advances replay."""

import hashlib
import json
from copy import deepcopy
from typing import Any


class ReplayAdapter:
    def __init__(self, events: list[dict[str, Any]]):
        self._events = deepcopy(events)

    def fixture_digest(
        self, *, case_id: str, recipe_contract_version: int, fixture_version: int
    ) -> str:
        envelope = {
            "recipe_contract_version": recipe_contract_version,
            "fixture_version": fixture_version,
            "case_id": case_id,
            "events": self._events,
        }
        try:
            canonical = json.dumps(
                envelope,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ValueError("Replay fixture must contain finite JSON values") from exc
        return hashlib.sha256(canonical).hexdigest()

    def validate_progress(
        self, cursor: int, status: str, question: str | None, answer: str | None
    ) -> None:
        if type(cursor) is not int or not 0 <= cursor <= len(self._events):
            raise ValueError("Saved cursor is outside the replay schedule")
        if cursor == 0:
            if status not in {"working", "failed"} or answer is not None:
                raise ValueError("Initial replay state cannot contain released work")
            return
        previous = self._events[cursor - 1]
        if not isinstance(previous, dict):
            raise ValueError("Saved progress references an invalid event")
        if previous.get("kind") == "clarification":
            if status == "needs_input":
                if question != previous.get("question") or answer is not None:
                    raise ValueError("Pending question does not match its replay event")
            elif status not in {"working", "failed"} or not answer or not answer.strip():
                raise ValueError("Released clarification requires its answer")
        elif previous.get("kind") == "review":
            if status != "succeeded":
                raise ValueError("Released review requires completed delivery")
            has_question = any(
                isinstance(event, dict) and event.get("kind") == "clarification"
                for event in self._events[:cursor]
            )
            if has_question and (not answer or not answer.strip()):
                raise ValueError("Delivered replay cannot skip clarification")
        else:
            raise ValueError("Saved progress references an unsupported event")

    def advance(self, cursor: int, artifact_id: str, scope: tuple[str, ...]) -> dict:
        if cursor >= len(self._events):
            raise ValueError("Replay has no further event")
        event = deepcopy(self._events[cursor])
        if not isinstance(event, dict):
            raise ValueError("Replay event must be an object")
        if event.get("kind") == "review":
            feedback = event.get("feedback", {})
            if not isinstance(feedback, dict):
                raise ValueError("Replay review must be an object")
            if feedback.get("artifact_id") == "$artifact_id":
                feedback["artifact_id"] = artifact_id
            if feedback.get("scope") == "$scope":
                feedback["scope"] = list(scope)
        return event
