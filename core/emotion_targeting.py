"""Validate memory-event attribution against the active visibility domain."""

from __future__ import annotations

from typing import Any

from .models import clean_text


def memory_emotion_refs(memory: Any, ctx: Any) -> dict[str, dict[str, str]] | None:
    memory_session = clean_text(getattr(memory, "session_id", ""), 220)
    current_session = clean_text(getattr(ctx, "session_id", ""), 220)
    if memory_session and current_session and memory_session != current_session:
        return None
    subject = getattr(memory, "subject", None)
    subject_kind = clean_text(getattr(subject, "kind", ""), 24)
    subject_id = clean_text(getattr(subject, "id", ""), 160)
    user_id = clean_text(getattr(ctx, "user_id", ""), 160)
    bot_id = clean_text(getattr(ctx, "bot_id", ""), 160) or "self"
    if subject_kind == "user" and user_id and subject_id != user_id:
        return None
    if subject_kind not in {"user", "bot"} or not subject_id:
        return None
    return {
        "actor_ref": {"kind": subject_kind, "id": subject_id, "role": clean_text(getattr(subject, "role", ""), 40)},
        "target_ref": {"kind": "bot", "id": bot_id, "role": "bot_self"},
        "quoted_target_ref": {"kind": "unknown", "id": "", "role": ""},
    }


__all__ = ["memory_emotion_refs"]
