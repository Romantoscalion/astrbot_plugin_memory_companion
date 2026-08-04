from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from . import bot_personal_contract
from .bot_personal_dto import BotPersonalArchiveDTO, build_bot_personal_archive
from .capability_probe import CapabilityCache, PROFILE_NAMES as C4_PROFILE_NAMES, build_capability_snapshot
from .context_consumer import consume_context_projection
from .models import EntityRef, MemoryRecord, SessionContext, clean_text
from .person_projection import consume_person_projection


LOCAL_TZ = ZoneInfo("Asia/Shanghai")

_COMPANION_RELATIONSHIP_PHASES = {
    "deeply_distant",
    "strongly_distant",
    "distant",
    "acquaintance",
    "familiar",
    "close",
    "intimate",
    "deeply_bonded",
}
_COMPANION_INTERACTION_BANDS = {
    "avoidant",
    "hurt",
    "relaxed",
    "lively",
    "warm",
    "close",
    "affectionate",
}
_COMPANION_EXPRESSION_CONTRACT = "companion_interaction_expression.v1"
_COMPANION_EXPRESSION_BEHAVIORS = {
    "acknowledge",
    "brief_reply",
    "give_space",
    "reply",
    "clarify",
    "light_humor",
    "followup",
    "support",
    "shared_ritual",
    "affectionate_expression",
}
_COMPANION_EXPRESSION_SAFETY_MODES = {
    "normal",
    "contact_boundary_passive",
    "contact_boundary",
    "p4_blocked",
}
_COMPANION_EXPRESSION_BLOCKERS = {"contact_boundary", "p4_safety"}
_COMPANION_EXPRESSION_REASON_CODES = {
    "relationship_baseline_retained",
    "interaction_band_applied",
    "administrator_override_applied",
    "owner_role_required",
    "contact_boundary_passive_reengagement",
    "p4_warmth_cap_applied",
    "relationship_tone_applied",
    "relationship_address_applied",
    "relationship_followup_cap",
    "intent_followup_suppressed",
    "low_energy_expression_cap",
    "down_mood_expression_cap",
    "up_mood_expression_lift",
    "relationship_proactive_cap",
    "interaction_proactive_suppressed",
    "schedule_proactive_suppressed",
    "p4_blocked",
    "contact_boundary",
}


def sanitize_companion_expression_decision(value: Any) -> dict[str, Any]:
    """Accept only the bounded, request-scoped Companion expression contract."""

    fallback = {"status": "invalid", "read_only": True, "decision": {}}
    if type(value) is not dict or value.get("contract") != _COMPANION_EXPRESSION_CONTRACT:
        return fallback
    expression_band = value.get("expression_band")
    if type(expression_band) is not str or expression_band not in _COMPANION_INTERACTION_BANDS:
        return fallback
    allowed_behaviors = value.get("allowed_behaviors")
    if type(allowed_behaviors) not in {list, tuple} or len(allowed_behaviors) > 12:
        return fallback
    if any(type(item) is not str or item not in _COMPANION_EXPRESSION_BEHAVIORS for item in allowed_behaviors):
        return fallback
    if len(set(allowed_behaviors)) != len(allowed_behaviors):
        return fallback
    safety_mode = value.get("safety_mode")
    if type(safety_mode) is not str or safety_mode not in _COMPANION_EXPRESSION_SAFETY_MODES:
        return fallback
    blocker = value.get("blocker")
    if blocker is not None and (type(blocker) is not str or blocker not in _COMPANION_EXPRESSION_BLOCKERS):
        return fallback
    reason_codes = value.get("reason_codes")
    if type(reason_codes) not in {list, tuple} or len(reason_codes) > 24:
        return fallback
    if any(type(item) is not str or item not in _COMPANION_EXPRESSION_REASON_CODES for item in reason_codes):
        return fallback
    if type(value.get("followup")) is not bool:
        return fallback
    return {
        "status": "accepted",
        "read_only": True,
        "decision": {
            "contract": _COMPANION_EXPRESSION_CONTRACT,
            "expression_band": expression_band,
            "allowed_behaviors": list(allowed_behaviors),
            "safety_mode": safety_mode,
            "blocker": blocker,
            "reason_codes": list(reason_codes),
        },
    }


def sanitize_companion_relationship_projection(value: Any) -> dict[str, Any]:
    fallback = {"status": "invalid", "read_only": True, "projection": {}}
    if type(value) is not dict:
        return fallback
    if value.get("schema_version") != "chat.relationship_projection.v1":
        return fallback
    if value.get("authority") != "private_companion.relationship_score" or value.get("read_only") is not True:
        return fallback
    phase_key = value.get("phase_key")
    if type(phase_key) is not str or phase_key not in _COMPANION_RELATIONSHIP_PHASES:
        return fallback
    score = value.get("score")
    if type(score) is not int or not -1200 <= score <= 1200:
        return fallback
    soft = value.get("soft_behaviors")
    if type(soft) is not dict or any(type(item) is not bool for item in soft.values()):
        return fallback
    try:
        proactive_care_limit = int(value.get("proactive_care_limit") or 0)
    except (TypeError, ValueError):
        proactive_care_limit = 0
    projection = {
        "schema_version": "chat.relationship_projection.v1",
        "authority": "private_companion.relationship_score",
        "read_only": True,
        "score": score,
        "phase_key": phase_key,
        "phase_label": clean_text(value.get("phase_label"), 40),
        "tone": clean_text(value.get("tone"), 160),
        "address_level": clean_text(value.get("address_level"), 120),
        "proactive_care_limit": max(0, min(30, proactive_care_limit)),
        "soft_behaviors": {
            key: bool(soft.get(key, False))
            for key in (
                "allow_playful_jokes",
                "allow_followup",
                "allow_memory_mention",
                "allow_daily_care",
            )
        },
    }
    relationship_mode = value.get("relationship_mode")
    if relationship_mode in {"normal", "owner_exclusive"}:
        projection["relationship_mode"] = relationship_mode
    current_interaction = value.get("current_interaction")
    if type(current_interaction) is dict:
        expression_band = current_interaction.get("expression_band")
        if type(expression_band) is str and expression_band in _COMPANION_INTERACTION_BANDS:
            projection["current_interaction"] = {
                "expression_band": expression_band,
                "label": clean_text(current_interaction.get("label"), 40),
                "source": clean_text(current_interaction.get("source"), 40),
                "reason": clean_text(current_interaction.get("reason"), 120),
                "manual_override": current_interaction.get("manual_override") is True,
            }
    return {"status": "accepted", "read_only": True, "projection": projection}


def _local_time_label(value: Any) -> str:
    text = clean_text(value, 80)
    if not text:
        return ""
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return text


class MemoryCompanionBridge:
    """Public bridge for other plugins.

    The bridge intentionally accepts structured fields. A caller should say
    whether something is a bot action, a persona-life fragment, a real user
    fact, or an imported summary instead of handing over vague prose.
    """

    def __init__(self, plugin: Any):
        self._plugin = plugin
        self._capability_cache = CapabilityCache()

    def consume_relationship_projection(self, projection: Any) -> dict[str, Any]:
        """Validate a read-only Companion relationship projection without persisting it."""
        return sanitize_companion_relationship_projection(projection)

    async def record_event(
        self,
        *,
        content: str,
        memory_type: str = "external_event",
        scope: str = "unknown",
        session_id: str = "",
        platform: str = "",
        message_id: str = "",
        group_id: str = "",
        subject: dict[str, Any] | None = None,
        object: dict[str, Any] | None = None,
        visibility: str = "bot_self",
        sayability: str = "direct",
        reality_level: str = "bot_action",
        lifecycle: str = "stable_memory",
        confidence: float = 0.85,
        importance: float = 0.5,
        review_status: str = "auto",
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        source_plugin: str = "external",
        memory_id: str = "",
        occurred_at: str = "",
    ) -> str:
        return await self._plugin.record_external_event(
            content=content,
            memory_type=memory_type,
            scope=scope,
            session_id=session_id,
            platform=platform,
            message_id=message_id,
            group_id=group_id,
            subject=self._entity(subject) if subject else EntityRef.bot_self(),
            object=self._entity(object) if object else EntityRef(kind="session", id=session_id, role="target_session"),
            visibility=visibility,
            sayability=sayability,
            reality_level=reality_level,
            lifecycle=lifecycle,
            confidence=confidence,
            importance=importance,
            review_status=review_status,
            tags=tags or [],
            metadata=metadata or {},
            source_plugin=source_plugin,
            memory_id=memory_id,
            occurred_at=occurred_at,
        )

    async def record_bot_action(self, *, content: str, **kwargs: Any) -> str:
        kwargs.setdefault("memory_type", "self_action")
        kwargs.setdefault("visibility", "bot_self")
        kwargs.setdefault("reality_level", "bot_action")
        kwargs.setdefault("source_plugin", kwargs.get("source_plugin", "external"))
        return await self.record_event(content=content, **kwargs)

    async def record_persona_life(self, *, content: str, **kwargs: Any) -> str:
        kwargs.setdefault("memory_type", "persona_life")
        kwargs.setdefault("visibility", "bot_self")
        kwargs.setdefault("reality_level", "persona_life")
        kwargs.setdefault("sayability", "indirect")
        return await self.record_event(content=content, **kwargs)

    async def record_proactive_message(self, *, content: str, **kwargs: Any) -> str:
        kwargs.setdefault("memory_type", "proactive_message")
        kwargs.setdefault("visibility", "bot_self")
        kwargs.setdefault("reality_level", "bot_action")
        kwargs.setdefault("tags", ["proactive", "bot_action"])
        kwargs.setdefault("importance", 0.55)
        return await self.record_event(content=content, **kwargs)

    async def record_visible_turn(self, *, role: str, content: str, **kwargs: Any) -> str:
        """Record a real visible chat turn into the short-term timeline only."""
        return await self._plugin.record_visible_turn(role=role, content=content, **kwargs)

    async def record_shared_experience(
        self,
        *,
        content: str,
        experience_type: str,
        bot_id: str = "",
        bot_name: str = "",
        user_id: str = "",
        user_name: str = "",
        scope: str = "private",
        session_id: str = "",
        platform: str = "",
        source_plugin: str = "external",
        memory_id: str = "",
        confidence: float = 0.9,
        importance: float = 0.7,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Record one distilled call/watch experience with explicit ownership."""
        normalized_type = clean_text(experience_type, 40).lower()
        if normalized_type in {"watch", "shared_watch", "video"}:
            memory_type = "shared_watch"
            experience_tag = "watch"
        elif normalized_type in {"call", "shared_call", "voice"}:
            memory_type = "shared_call"
            experience_tag = "call"
        else:
            memory_type = "shared_experience"
            experience_tag = normalized_type or "shared"
        subject = EntityRef.bot_self(bot_id=bot_id, bot_name=bot_name)
        target = EntityRef(
            kind="user",
            id=clean_text(user_id, 120),
            name=clean_text(user_name, 80),
            role="shared_experience_partner",
        )
        return await self.record_event(
            content=content,
            memory_type=memory_type,
            scope=scope,
            session_id=session_id,
            platform=platform,
            subject={
                "kind": subject.kind,
                "id": subject.id,
                "name": subject.name,
                "role": subject.role,
            },
            object={
                "kind": target.kind,
                "id": target.id,
                "name": target.name,
                "role": target.role,
            },
            visibility="bot_self",
            sayability="direct",
            reality_level="bot_action",
            lifecycle="stable_memory",
            confidence=confidence,
            importance=importance,
            review_status="auto",
            tags=["shared_experience", experience_tag, "bot_action"],
            metadata=metadata or {},
            source_plugin=source_plugin,
            memory_id=memory_id,
        )

    async def record_search_action(self, *, content: str, **kwargs: Any) -> str:
        kwargs.setdefault("memory_type", "search_action")
        kwargs.setdefault("visibility", "bot_self")
        kwargs.setdefault("reality_level", "bot_action")
        kwargs.setdefault("tags", ["search", "bot_action"])
        kwargs.setdefault("importance", 0.62)
        return await self.record_event(content=content, **kwargs)

    async def record_creative_work(self, *, content: str, **kwargs: Any) -> str:
        kwargs.setdefault("memory_type", "creative_work")
        kwargs.setdefault("visibility", "bot_self")
        kwargs.setdefault("reality_level", "fictional_content")
        kwargs.setdefault("sayability", "direct")
        kwargs.setdefault("tags", ["creative_work"])
        kwargs.setdefault("importance", 0.72)
        return await self.record_event(content=content, **kwargs)

    async def record_image_action(self, *, content: str, **kwargs: Any) -> str:
        kwargs.setdefault("memory_type", "image_action")
        kwargs.setdefault("visibility", "bot_self")
        kwargs.setdefault("reality_level", "bot_action")
        kwargs.setdefault("tags", ["image", "bot_action"])
        kwargs.setdefault("importance", 0.6)
        return await self.record_event(content=content, **kwargs)

    async def record_qzone_action(self, *, content: str, **kwargs: Any) -> str:
        kwargs.setdefault("memory_type", "qzone_action")
        kwargs.setdefault("visibility", "bot_self")
        kwargs.setdefault("reality_level", "bot_action")
        kwargs.setdefault("tags", ["qzone", "bot_action"])
        kwargs.setdefault("importance", 0.58)
        return await self.record_event(content=content, **kwargs)

    async def record_reading(self, *, content: str, **kwargs: Any) -> str:
        kwargs.setdefault("memory_type", "reading_memory")
        kwargs.setdefault("visibility", "bot_self")
        kwargs.setdefault("reality_level", "bot_action")
        kwargs.setdefault("tags", ["reading", "bot_action"])
        kwargs.setdefault("importance", 0.55)
        return await self.record_event(content=content, **kwargs)

    async def record_schedule_fragment(self, *, content: str, **kwargs: Any) -> str:
        kwargs.setdefault("memory_type", "schedule_fragment")
        kwargs.setdefault("visibility", "bot_self")
        kwargs.setdefault("reality_level", "persona_life")
        kwargs.setdefault("sayability", "indirect")
        kwargs.setdefault("tags", ["schedule", "persona_life"])
        kwargs.setdefault("importance", 0.45)
        return await self.record_event(content=content, **kwargs)

    async def record_bot_personal_archive(self, envelope: BotPersonalArchiveDTO | dict[str, Any]) -> dict[str, Any]:
        """Send one validated Bot Personal archive envelope without leaking failures."""
        base = {
            "ok": False,
            "record_id": "",
            "deduplicated": False,
            "version": 0,
            "error_code": None,
            "state": "degraded",
        }
        try:
            dto = build_bot_personal_archive(envelope)
        except Exception as exc:
            return {**base, "state": "invalid", "error_code": getattr(exc, "error_code", "invalid")}
        try:
            recorder = getattr(self._plugin, "record_bot_personal_archive", None)
        except Exception:
            recorder = None
        if not callable(recorder):
            return {**base, "error_code": "bridge_method_unavailable", "state": "degraded"}
        try:
            result = await recorder(dto)
        except Exception:
            return {**base, "error_code": "bridge_exception", "state": "degraded"}
        if not isinstance(result, dict):
            return {**base, "error_code": "invalid_bridge_response", "state": "degraded"}
        normalized = dict(base)
        for key in base:
            if key in result:
                normalized[key] = result[key]
        normalized["ok"] = bool(result.get("ok"))
        normalized["deduplicated"] = bool(result.get("deduplicated"))
        normalized["version"] = int(result.get("version") or 0)
        if normalized["ok"]:
            normalized["state"] = "deduplicated" if normalized["deduplicated"] else "sent"
        elif normalized["state"] == "ready":
            normalized["state"] = "degraded"
        return normalized

    async def record_bot_personal_memory(self, *, memory_type: str, payload: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
        """Compatibility alias that still crosses the structured archive boundary."""
        try:
            envelope = build_bot_personal_archive(memory_type=memory_type, payload=payload or {}, **kwargs)
        except Exception as exc:
            return {
                "ok": False, "record_id": "", "deduplicated": False, "version": 0,
                "error_code": getattr(exc, "error_code", "invalid"), "state": "invalid",
            }
        return await self.record_bot_personal_archive(envelope)

    async def read_bot_personal_profile(self, query: str = "", *, limit: int = 10) -> dict[str, Any]:
        """Read only safe Bot Personal summaries; never return archive payloads."""
        base = {"ok": False, "read_only": True, "state": "degraded", "degraded": True, "pending": True, "items": []}
        try:
            getter = getattr(self._plugin, "read_bot_personal_profile", None)
        except Exception:
            getter = None
        if not callable(getter):
            return {**base, "error_code": "bridge_method_unavailable"}
        try:
            result = await getter(query=query, limit=limit)
        except Exception:
            return {**base, "error_code": "bridge_exception"}
        if not isinstance(result, dict):
            return {**base, "error_code": "invalid_bridge_response"}
        safe_keys = {
            "record_id", "memory_type", "memory_domain", "subject", "date", "window", "occurred_at",
            "source_kind", "source_refs", "evidence_level", "status", "version", "summary", "reference",
        }
        items = result.get("items", result.get("memories", []))
        safe_items: list[dict[str, Any]] = []
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            safe_items.append({key: item[key] for key in safe_keys if key in item and key not in {"payload", "content"}})
        return {
            "ok": bool(result.get("ok", True)), "read_only": True,
            "state": "ready" if result.get("state") in (None, "ready") else result.get("state"),
            "degraded": bool(result.get("degraded", False)), "pending": bool(result.get("pending", False)),
            "items": safe_items,
        }

    async def read_user_memory_summary(
        self,
        user_id: str,
        *,
        session_id: str = "",
        limit: int = 6,
    ) -> dict[str, Any]:
        """Read a strict, exact-user Memory summary without exposing memory text."""

        identity = clean_text(user_id, 120)
        safe_session = clean_text(session_id, 200)
        base = {
            "contract": "memory.user_memory_summary.v1",
            "ok": False,
            "read_only": True,
            "state": "degraded",
            "degraded": True,
            "pending": True,
            "user_id": identity,
            "session_id": safe_session,
            "counts": {"profile": 0, "preference": 0, "relationship": 0, "private_conversation": 0, "other": 0, "total": 0},
            "summaries": [],
            "workspace": {"kind": "memory_user_workspace", "route_hint": "user_memory", "user_id": identity},
        }
        if not identity:
            return {**base, "error_code": "missing_user_id"}
        try:
            getter = getattr(self._plugin, "read_user_memory_summary", None)
        except Exception:
            getter = None
        if not callable(getter):
            return {**base, "error_code": "bridge_method_unavailable"}
        try:
            result = await getter(identity, session_id=safe_session, limit=limit)
        except Exception:
            return {**base, "error_code": "bridge_exception"}
        if not isinstance(result, dict) or result.get("contract") != base["contract"]:
            return {**base, "error_code": "invalid_bridge_response"}

        counts = dict(base["counts"])
        raw_counts = result.get("counts") if isinstance(result.get("counts"), dict) else {}
        for key in counts:
            try:
                counts[key] = max(0, int(raw_counts.get(key, 0)))
            except (TypeError, ValueError, OverflowError):
                counts[key] = 0
        summaries: list[dict[str, Any]] = []
        for item in result.get("summaries", []) if isinstance(result.get("summaries"), list) else []:
            if not isinstance(item, dict):
                continue
            category = clean_text(item.get("category"), 40)
            if category not in {"profile", "preference", "relationship", "private_conversation", "other"}:
                continue
            summaries.append(
                {
                    "category": category,
                    "memory_type": clean_text(item.get("memory_type"), 80),
                    "occurred_at": clean_text(item.get("occurred_at"), 80),
                    "summary": clean_text(item.get("summary"), 100),
                    "content_redacted": True,
                    "truncated": True,
                }
            )
            if len(summaries) >= 8:
                break
        state = clean_text(result.get("state"), 40)
        return {
            **base,
            "ok": bool(result.get("ok")) and state == "ready",
            "state": "ready" if state == "ready" else "degraded",
            "degraded": state != "ready" or bool(result.get("degraded", False)),
            "pending": state != "ready" or bool(result.get("pending", False)),
            "user_id": identity,
            "session_id": safe_session,
            "counts": counts,
            "summaries": summaries,
            "workspace": base["workspace"],
            **({"error_code": clean_text(result.get("error_code"), 80)} if state != "ready" and clean_text(result.get("error_code"), 80) else {}),
        }

    async def search_bot_personal_profile(self, query: str = "", *, limit: int = 10) -> dict[str, Any]:
        return await self.read_bot_personal_profile(query=query, limit=limit)

    async def read_bot_profile(
        self,
        profile: str,
        query: str = "",
        *,
        limit: int = 10,
        current_date: str = "",
        current_window: str = "",
        authorized: bool = False,
    ) -> dict[str, Any]:
        """Read a C4 Bot Profile through a privacy-limited bridge boundary."""

        base = {
            "ok": False,
            "read_only": True,
            "state": "degraded",
            "degraded": True,
            "pending": True,
            "profile": clean_text(profile, 80),
            "items": [],
            "warnings": [],
        }
        try:
            getter = getattr(self._plugin, "read_bot_profile", None)
        except Exception:
            getter = None
        if not callable(getter):
            return {**base, "error_code": "bridge_method_unavailable"}
        try:
            result = await getter(
                profile,
                query=query,
                limit=limit,
                current_date=current_date,
                current_window=current_window,
                authorized=authorized,
            )
        except Exception:
            return {**base, "error_code": "bridge_exception"}
        if not isinstance(result, dict):
            return {**base, "error_code": "invalid_bridge_response"}
        safe_item_keys = {
            "record_id", "memory_domain", "memory_type", "subject", "date", "window",
            "occurred_at", "source_kind", "source_refs", "evidence_level", "status",
            "version", "summary", "reference",
        }
        safe_items: list[dict[str, Any]] = []
        items = result.get("items", [])
        for item in items if isinstance(items, list) else []:
            if isinstance(item, dict):
                safe_items.append({key: item[key] for key in safe_item_keys if key in item})
        return {
            "ok": bool(result.get("ok", True)),
            "read_only": True,
            "state": clean_text(result.get("state"), 40) or "ready",
            "degraded": bool(result.get("degraded", False)),
            "pending": bool(result.get("pending", False)),
            "profile": clean_text(result.get("profile") or profile, 80),
            "items": safe_items,
            "warnings": [clean_text(item, 160) for item in result.get("warnings", []) if clean_text(item, 160)][:8]
            if isinstance(result.get("warnings"), list) else [],
        }

    async def read_profile(self, profile: str, query: str = "", **kwargs: Any) -> dict[str, Any]:
        """Short alias for callers that use the generic Profile API name."""

        return await self.read_bot_profile(profile, query=query, **kwargs)

    async def search(
        self,
        query: str,
        *,
        session_context: SessionContext | dict[str, Any] | None = None,
        top_k: int | None = None,
        p5_attestation: Any = None,
        p5_attestation_consumer: Any = None,
    ) -> list[dict[str, Any]]:
        return await self._plugin.bridge_search(
            query,
            session_context=session_context,
            top_k=top_k,
            p5_attestation=p5_attestation,
            p5_attestation_consumer=p5_attestation_consumer,
        )

    async def compose_injection(
        self,
        query: str,
        *,
        session_context: SessionContext | dict[str, Any] | None = None,
        top_k: int | None = None,
        max_chars: int | None = None,
        companion_bot_mood: str = "",
        companion_bot_energy: float = 0.0,
        p5_attestation: Any = None,
        p5_attestation_consumer: Any = None,
    ) -> str:
        return await self._plugin.bridge_compose_injection(
            query,
            session_context=session_context,
            top_k=top_k,
            max_chars=max_chars,
            companion_bot_mood=companion_bot_mood,
            companion_bot_energy=companion_bot_energy,
            p5_attestation=p5_attestation,
            p5_attestation_consumer=p5_attestation_consumer,
        )

    async def compose_context(
        self,
        *,
        query: str = "",
        session_context: SessionContext | dict[str, Any] | None = None,
        top_k: int | None = None,
        max_chars: int | None = None,
        companion_bot_mood: str = "",
        companion_bot_energy: float = 0.0,
        retrieval_profile: str = "",
        p5_attestation: Any = None,
        p5_attestation_consumer: Any = None,
    ) -> str:
        return await self._plugin.bridge_compose_context(
            query=query,
            session_context=session_context,
            top_k=top_k,
            max_chars=max_chars,
            companion_bot_mood=companion_bot_mood,
            companion_bot_energy=companion_bot_energy,
            retrieval_profile=retrieval_profile,
            p5_attestation=p5_attestation,
            p5_attestation_consumer=p5_attestation_consumer,
        )

    async def remember(self, *, event: Any, content: str, note_type: str = "memory") -> dict[str, Any]:
        return await self._plugin.tool_remember(event, content, note_type=note_type)

    async def recall(
        self,
        *,
        event: Any,
        query: str,
        top_k: int = 5,
        p5_attestation: Any = None,
        p5_attestation_consumer: Any = None,
    ) -> dict[str, Any]:
        return await self._plugin.tool_recall(
            event,
            query,
            top_k=top_k,
            p5_attestation=p5_attestation,
            p5_attestation_consumer=p5_attestation_consumer,
        )

    def p5_capability_status(self) -> dict[str, Any]:
        getter = getattr(self._plugin, "p5_capability_status", None)
        if not callable(getter):
            return {"state": "degraded", "error_code": "p5_status_unavailable"}
        try:
            result = getter()
        except Exception:
            return {"state": "degraded", "error_code": "p5_status_exception"}
        return dict(result) if isinstance(result, dict) else {"state": "degraded", "error_code": "p5_status_invalid"}

    def provenance_snapshot(self) -> dict[str, Any]:
        getter = getattr(self._plugin, "provenance_snapshot", None)
        if not callable(getter):
            return {"records": {}, "operation_count": 0, "state": "degraded"}
        result = getter()
        return dict(result) if isinstance(result, dict) else {"records": {}, "operation_count": 0, "state": "degraded"}

    def provenance_preview(self, candidates: list[dict[str, Any]], *, operation_ref_hash: str) -> dict[str, Any]:
        getter = getattr(self._plugin, "provenance_preview", None)
        if not callable(getter):
            return {"mode": "preview", "readonly": True, "write_count": 0, "error_codes": ["unavailable"]}
        result = getter(candidates, operation_ref_hash=operation_ref_hash)
        return dict(result) if isinstance(result, dict) else {"mode": "preview", "readonly": True, "write_count": 0, "error_codes": ["invalid_result"]}

    async def provenance_apply(self, operation: dict[str, Any]) -> dict[str, Any]:
        getter = getattr(self._plugin, "provenance_apply", None)
        if not callable(getter):
            return {"ok": False, "state": "degraded", "error_code": "unavailable"}
        result = await getter(operation)
        return dict(result) if isinstance(result, dict) else {"ok": False, "state": "degraded", "error_code": "invalid_result"}

    async def provenance_backup(self) -> dict[str, Any]:
        getter = getattr(self._plugin, "provenance_backup", None)
        if not callable(getter):
            return {"ok": False, "state": "degraded", "error_code": "unavailable"}
        result = await getter()
        return dict(result) if isinstance(result, dict) else {"ok": False, "state": "degraded", "error_code": "invalid_result"}

    async def provenance_rollback(self, operation: dict[str, Any]) -> dict[str, Any]:
        getter = getattr(self._plugin, "provenance_rollback", None)
        if not callable(getter):
            return {"ok": False, "state": "degraded", "error_code": "unavailable"}
        result = await getter(operation)
        return dict(result) if isinstance(result, dict) else {"ok": False, "state": "degraded", "error_code": "invalid_result"}

    async def create_note(self, *, event: Any, title: str, content: str = "") -> dict[str, Any]:
        return await self._plugin.tool_note_create(event, title, content)

    async def read_notes(self, *, event: Any, query: str = "", limit: int = 5) -> dict[str, Any]:
        return await self._plugin.tool_note_read(event, query, limit=limit)

    async def delete_note(self, *, event: Any, memory_id: str = "", title: str = "") -> dict[str, Any]:
        return await self._plugin.tool_note_delete(event, memory_id, title=title)

    def coordination_status(self) -> dict[str, Any]:
        try:
            getter = getattr(self._plugin, "companion_coordination_status", None)
        except Exception:
            return {"available": False, "state": "degraded", "degraded": True, "reason": "bridge_exception"}
        if not callable(getter):
            return {"available": False, "state": "degraded", "degraded": True, "reason": "method_missing"}
        try:
            result = getter()
        except Exception as exc:
            return {"available": False, "state": "degraded", "degraded": True, "reason": "bridge_exception", "error": str(exc)[:160]}
        if not isinstance(result, dict):
            return {"available": False, "state": "degraded", "degraded": True, "reason": "invalid_status"}
        result = dict(result)
        result.setdefault("available", True)
        result.setdefault("state", "ready")
        result.setdefault("degraded", False)
        return result

    def consume_person_projection(
        self,
        projection: Any,
        expected_identity_key: str = "",
        expected_person_id: str = "",
        *,
        companion_available: bool = True,
    ) -> dict[str, Any]:
        """Validate a companion-owned person projection without writing memory state."""
        return consume_person_projection(
            projection,
            expected_identity_key=expected_identity_key,
            expected_person_id=expected_person_id,
            companion_available=companion_available,
        )

    def consume_context_projection(
        self,
        context: Any,
        expected_person_id: str = "",
        expected_scope: str = "",
        *,
        companion_available: bool = True,
    ) -> dict[str, Any]:
        """Validate a P3 projection without creating people or storing raw text."""
        return consume_context_projection(
            context,
            expected_person_id=expected_person_id,
            expected_scope=expected_scope,
            companion_available=companion_available,
        )

    def probe_capability_snapshot(self) -> dict[str, Any]:
        """Return the C4 capability snapshot without touching plugin state or storage.

        The probe is intentionally based only on the shared contract module. It
        must remain safe to call from ordinary chat paths even when the contract
        is stale or the local module is otherwise malformed.
        """
        if self._capability_cache.snapshot().get("state") == "negative":
            return self.capability_status()
        try:
            descriptor = bot_personal_contract.capability_descriptor(
                available=True,
                read_only=False,
            )
        except Exception:
            return self._negative_personal_capability_probe("contract_descriptor_exception")

        if not isinstance(descriptor, dict):
            return self._negative_personal_capability_probe("contract_descriptor_invalid")

        result = dict(descriptor)
        try:
            problems = bot_personal_contract.contract_self_check()
        except Exception:
            return self._negative_personal_capability_probe(
                "contract_self_check_exception",
                base=result,
            )

        if not isinstance(problems, list):
            return self._negative_personal_capability_probe(
                "contract_self_check_invalid",
                base=result,
            )
        if problems:
            warnings = ["contract_self_check_failed"]
            known_codes = {
                "contract_fingerprint_stale",
                "duplicate_window_slug",
                "type_contracts_out_of_sync",
                "window_coverage_gap",
                "alias_points_to_unknown_window",
            }
            for problem in problems:
                code = str(problem).split(":", 1)[0]
                if code in known_codes and code not in warnings:
                    warnings.append(code)
            return self._negative_personal_capability_probe(
                "contract_self_check_failed",
                base=result,
                warnings=warnings,
            )

        result["available"] = True
        result["state"] = "available"
        result["degraded"] = False
        self._add_personal_capability_contract_aliases(result)
        c4_snapshot = build_capability_snapshot(
            available=True,
            state="available",
            contract_module=bot_personal_contract,
            methods=result.get("methods", []),
            profiles=C4_PROFILE_NAMES,
            warnings=result.get("warnings", []),
        )
        result.update(c4_snapshot)
        result["memory_domain"] = bot_personal_contract.BOT_PERSONAL_MEMORY_DOMAIN
        result["domain"] = bot_personal_contract.BOT_PERSONAL_MEMORY_DOMAIN
        result["contract_revision"] = bot_personal_contract.CONTRACT_REVISION
        result["capability_schema_version"] = bot_personal_contract.BOT_PERSONAL_CAPABILITY_SCHEMA_VERSION
        result["payload_schema_version"] = bot_personal_contract.BOT_PERSONAL_PAYLOAD_SCHEMA_VERSION
        result["capability_state"] = "available"
        p5_status = getattr(self._plugin, "p5_capability_status", None)
        if callable(p5_status):
            try:
                result["p5"] = dict(p5_status() or {})
            except Exception:
                result["p5"] = {"state": "degraded", "error_code": "p5_status_exception"}
        self._capability_cache.mark_available(c4_snapshot)
        result.setdefault("warnings", [])
        return result

    def probe_bot_personal_memory_capabilities(self) -> dict[str, Any]:
        """Backward-compatible C1 probe; C4 state is exposed as capability_state."""

        result = dict(self.probe_capability_snapshot())
        if result.get("capability_state") == "available":
            result["state"] = "ready"
        result["legacy_state"] = result.get("state", "degraded")
        return result

    def capability_status(self) -> dict[str, Any]:
        """Return the bounded C4 cache state without probing storage."""

        snapshot = self._capability_cache.snapshot()
        snapshot["read_only"] = False
        snapshot["contract_name"] = bot_personal_contract.CONTRACT_NAME
        snapshot["max_payload_bytes"] = bot_personal_contract.BOT_PERSONAL_MAX_PAYLOAD_BYTES
        snapshot["memory_domain"] = bot_personal_contract.BOT_PERSONAL_MEMORY_DOMAIN
        snapshot["domain"] = snapshot["memory_domain"]
        snapshot["contract_revision"] = bot_personal_contract.CONTRACT_REVISION
        snapshot["capability_schema_version"] = bot_personal_contract.BOT_PERSONAL_CAPABILITY_SCHEMA_VERSION
        snapshot["payload_schema_version"] = bot_personal_contract.BOT_PERSONAL_PAYLOAD_SCHEMA_VERSION
        snapshot["capability_state"] = snapshot.get("state", "unprobed")
        return snapshot

    def mark_capability_negative(self, reason: str) -> dict[str, Any]:
        """Temporarily suppress repeated capability failures at the bridge edge."""

        self._capability_cache.mark_negative(clean_text(reason, 120) or "capability_negative")
        return self.capability_status()

    @staticmethod
    def _add_personal_capability_contract_aliases(result: dict[str, Any]) -> dict[str, Any]:
        """Expose stable C1 aliases without changing the shared contract copy."""

        result.setdefault("domain", result.get("memory_domain", ""))
        result.setdefault("domains", [result.get("memory_domain", "")])
        result.setdefault("profiles", list(C4_PROFILE_NAMES))
        result.setdefault("legacy_profiles", ["bot_personal_archive"])
        result.setdefault(
            "methods",
            [
                "record_event",
                "record_visible_turn",
                "record_bot_personal_archive",
                "record_bot_personal_memory",
                "read_bot_personal_profile",
                "search_bot_personal_profile",
                "search",
                "compose_injection",
                "compose_context",
                "remember",
                "recall",
                "consume_person_projection",
                "consume_context_projection",
                "read_bot_profile",
                "read_profile",
                "p5_capability_status",
                "provenance_snapshot",
                "provenance_preview",
                "provenance_apply",
                "provenance_backup",
                "provenance_rollback",
                "probe_capability_snapshot",
                "probe_bot_personal_memory_capabilities",
            ],
        )
        result.setdefault("contract_version", str(result.get("contract_revision", "")))
        result.setdefault("schema_version", str(result.get("capability_schema_version", "")))
        return result

    def _negative_personal_capability_probe(
        self,
        reason: str,
        *,
        base: dict[str, Any] | None = None,
        warnings: list[str] | None = None,
    ) -> dict[str, Any]:
        """Return a bounded failure and suppress repeated probes for the TTL."""

        safe_reason = clean_text(reason, 120) or "capability_negative"
        result = self._degraded_personal_capability_probe(
            safe_reason,
            base=base,
            warnings=warnings,
        )
        cached = self._capability_cache.mark_negative(safe_reason)
        for key in ("available", "state", "degraded", "pending", "error_code"):
            if key in cached:
                result[key] = cached[key]
        result["available"] = False
        result["state"] = "negative"
        result["capability_state"] = "negative"
        result["degraded"] = True
        result["pending"] = False
        result["error_code"] = safe_reason
        result["p5"] = {"state": "degraded", "error_code": safe_reason}
        return result

    @staticmethod
    def _degraded_personal_capability_probe(
        reason: str,
        *,
        base: dict[str, Any] | None = None,
        warnings: list[str] | None = None,
    ) -> dict[str, Any]:
        result = {
            "available": False,
            "read_only": False,
            "memory_domain": getattr(bot_personal_contract, "BOT_PERSONAL_MEMORY_DOMAIN", ""),
            "contract_name": getattr(bot_personal_contract, "CONTRACT_NAME", ""),
            "contract_revision": getattr(bot_personal_contract, "CONTRACT_REVISION", 0),
            "contract_fingerprint": getattr(bot_personal_contract, "CONTRACT_FINGERPRINT", ""),
            "capability_schema_version": getattr(
                bot_personal_contract, "BOT_PERSONAL_CAPABILITY_SCHEMA_VERSION", ""
            ),
            "payload_schema_version": getattr(
                bot_personal_contract, "BOT_PERSONAL_PAYLOAD_SCHEMA_VERSION", ""
            ),
            "windows": list(getattr(bot_personal_contract, "WINDOW_SLUGS", ())),
            "memory_types": list(getattr(bot_personal_contract, "BOT_PERSONAL_MEMORY_TYPES", ())),
            "max_payload_bytes": getattr(bot_personal_contract, "BOT_PERSONAL_MAX_PAYLOAD_BYTES", 0),
            "warnings": [],
        }
        result.update(base or {})
        result.update(
            {
                "available": False,
                "state": "degraded",
                "degraded": True,
                "warnings": list(warnings or [reason]),
            }
        )
        MemoryCompanionBridge._add_personal_capability_contract_aliases(result)
        c4_snapshot = build_capability_snapshot(
            available=False,
            state="degraded",
            contract_module=bot_personal_contract,
            methods=result.get("methods", []),
            profiles=C4_PROFILE_NAMES,
            warnings=result.get("warnings", []),
            error_code=reason,
        )
        result.update(c4_snapshot)
        result["memory_domain"] = getattr(bot_personal_contract, "BOT_PERSONAL_MEMORY_DOMAIN", "")
        result["domain"] = result["memory_domain"]
        result["contract_revision"] = getattr(bot_personal_contract, "CONTRACT_REVISION", 0)
        result["capability_schema_version"] = getattr(
            bot_personal_contract, "BOT_PERSONAL_CAPABILITY_SCHEMA_VERSION", ""
        )
        result["payload_schema_version"] = getattr(
            bot_personal_contract, "BOT_PERSONAL_PAYLOAD_SCHEMA_VERSION", ""
        )
        result["capability_state"] = "degraded"
        result["p5"] = {"state": "degraded", "error_code": reason}
        return result

    def get_token_usage_summary(self) -> dict[str, Any]:
        getter = getattr(self._plugin, "token_usage_summary", None)
        if callable(getter):
            result = getter()
            return result if isinstance(result, dict) else {}
        return {}

    def should_defer_private_companion_section(self, section: str) -> bool:
        checker = getattr(self._plugin, "should_private_companion_defer_section", None)
        if callable(checker):
            return bool(checker(section))
        return False

    async def create_cross_window_thread(
        self,
        *,
        from_session: str,
        to_session: str,
        topic: str,
        content: str,
        visibility: str = "shareable",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        return await self._plugin.store.create_cross_window_thread(
            from_session=from_session,
            to_session=to_session,
            topic=topic,
            content=content,
            visibility=visibility,
            metadata=metadata or {},
        )

    async def mark_visibility(self, memory_id: str, visibility: str) -> bool:
        return await self._plugin.store.update_memory_visibility(memory_id, visibility)

    def get_emotional_events(self, *, session_id: str = "", limit: int = 5) -> list[dict[str, Any]]:
        """Retrieve pending emotional drift events for the companion plugin."""
        return self._plugin.bridge_get_emotional_events(session_id=session_id, limit=limit)

    async def record_emotion_event(self, event: dict[str, Any]) -> dict[str, Any]:
        """Persist one redacted event revision outside normal memory retrieval."""
        return await self._plugin.store.upsert_emotion_event(event)

    async def revise_emotion_event(self, event: dict[str, Any]) -> dict[str, Any]:
        """Persist a later revision of an existing event."""
        return await self._plugin.store.upsert_emotion_event(event)

    async def get_emotion_trace(self, trace_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        return await self._plugin.store.get_emotion_trace(trace_id, limit=limit)

    async def search_open_loops(self, *, session_id: str = "", limit: int = 3) -> list[dict[str, Any]]:
        """Search for unresolved open-loop / promise memories for proactive companionship."""
        return await self._plugin.bridge_search_open_loops(session_id=session_id, limit=limit)

    def get_relationship_phase(
        self,
        *,
        session_id: str = "",
        scope: str = "private",
        platform: str = "",
        user_id: str = "",
        group_id: str = "",
        bot_id: str = "",
    ) -> dict[str, Any]:
        """Return current relationship phase state for a session."""
        getter = getattr(self._plugin, "_get_relationship_phase", None)
        if not callable(getter):
            return {"phase": "unknown", "momentum": 0.0}
        normalizer = getattr(self._plugin, "session_context_from_bridge", None)
        payload = {
            "session_id": session_id,
            "scope": scope,
            "platform": platform,
            "user_id": user_id,
            "group_id": group_id,
            "bot_id": bot_id,
        }
        ctx = normalizer(payload) if callable(normalizer) else SessionContext(**payload)
        return getter(ctx)

    def peek_relationship_phase(
        self,
        *,
        session_id: str = "",
        scope: str = "private",
        platform: str = "",
        user_id: str = "",
        group_id: str = "",
        bot_id: str = "",
    ) -> dict[str, Any]:
        """Read an existing phase projection without creating default state."""
        fallback = {"observed": False, "phase": "unknown", "momentum_band": "unknown"}
        payload = {
            "session_id": session_id,
            "scope": scope,
            "platform": platform,
            "user_id": user_id,
            "group_id": group_id,
            "bot_id": bot_id,
        }
        if any(type(value) is not str for value in payload.values()):
            return fallback
        try:
            getter = getattr(self._plugin, "_peek_relationship_phase", None)
            if not callable(getter):
                return fallback
            normalizer = getattr(self._plugin, "session_context_from_bridge", None)
            ctx = normalizer(payload) if callable(normalizer) else SessionContext(**payload)
            result = getter(ctx)
        except Exception:
            return fallback
        if type(result) is not dict:
            return fallback
        for key in result:
            if type(key) is not str:
                return fallback

        observed = result.get("observed")
        phase = result.get("phase")
        momentum_band = result.get("momentum_band")
        if type(observed) is not bool or type(phase) is not str or type(momentum_band) is not str:
            return fallback
        if phase not in {"acquaintance", "familiar", "close", "intimate", "deeply_bonded"}:
            return fallback
        if momentum_band not in {"rising", "cooling", "steady"}:
            return fallback
        if not observed:
            return fallback
        projection: dict[str, Any] = {
            "observed": True,
            "phase": phase,
            "momentum_band": momentum_band,
        }
        touch_count = result.get("touch_count")
        if touch_count is not None:
            if type(touch_count) is not int or not 0 <= touch_count <= 256:
                return fallback
            projection["touch_count"] = touch_count
        return projection

    def get_recent_emotional_state(self) -> dict[str, Any]:
        """Return a summary of recent emotional events across ALL sessions.

        This provides cross-window emotional continuity for the companion plugin:
        if the bot recently touched scar or warm memories in any session, the
        companion plugin can factor this into its daily state calibration.
        """
        getter = getattr(self._plugin, "_get_cross_window_emotional_state", None)
        if not callable(getter):
            return {"total": 0, "scar_count": 0, "warm_count": 0, "vulnerable_count": 0}
        return getter()

    def _entity(self, payload: dict[str, Any]) -> EntityRef:
        return EntityRef(
            kind=str(payload.get("kind") or "user"),
            id=str(payload.get("id") or ""),
            name=str(payload.get("name") or ""),
            role=str(payload.get("role") or "unknown"),
        )

def serialize_memory(record: MemoryRecord, score: float | None = None, reason: str = "") -> dict[str, Any]:
    metadata = record.metadata if isinstance(record.metadata, dict) else {}
    key_facts = metadata.get("key_facts") if isinstance(metadata.get("key_facts"), list) else []
    routine_check_notes = metadata.get("routine_check_notes") if isinstance(metadata.get("routine_check_notes"), list) else []
    topics = metadata.get("topics") if isinstance(metadata.get("topics"), list) else []
    participants = metadata.get("participants") if isinstance(metadata.get("participants"), list) else []
    persona_weight_keys = [
        "persona_importance",
        "relationship_weight",
        "emotional_weight",
        "promise_weight",
        "open_loop_weight",
        "creative_weight",
        "preference_weight",
        "self_continuity_weight",
        "freshness_weight",
        "scar_weight",
        "emotional_debt_weight",
        "intimacy_weight",
        "vulnerability_weight",
    ]
    persona_weights = {
        key: metadata.get(key)
        for key in persona_weight_keys
        if metadata.get(key) is not None
    }
    data = {
        "id": record.id,
        "memory_type": record.memory_type,
        "scope": record.scope,
        "session_id": record.session_id,
        "group_id": record.group_id,
        "visibility": record.visibility,
        "sayability": record.sayability,
        "reality_level": record.reality_level,
        "lifecycle": record.lifecycle,
        "content": record.content,
        "evidence_preview": clean_text(record.evidence, 520),
        "canonical_summary": clean_text(metadata.get("canonical_summary"), 420),
        "key_facts": [clean_text(item, 180) for item in key_facts if clean_text(item, 180)][:4],
        "routine_check_notes": [clean_text(item, 180) for item in routine_check_notes if clean_text(item, 180)][:4],
        "topics": [clean_text(item, 80) for item in topics if clean_text(item, 80)][:5],
        "participants": [clean_text(item, 80) for item in participants if clean_text(item, 80)][:5],
        "memory_reason": clean_text(metadata.get("memory_reason"), 260),
        "mention_policy": clean_text(metadata.get("mention_policy"), 60),
        "mentionability_score": metadata.get("mentionability_score"),
        "relationship_phase": clean_text(metadata.get("relationship_phase"), 80),
        "decay_mode": clean_text(metadata.get("decay_mode"), 80),
        "active_dimensions": [
            clean_text(item, 80)
            for item in metadata.get("active_dimensions", [])
            if clean_text(item, 80)
        ][:6] if isinstance(metadata.get("active_dimensions"), list) else [],
        "persona_weights": persona_weights,
        "mention_feedback": metadata.get("mention_feedback") if isinstance(metadata.get("mention_feedback"), dict) else {},
        "confidence": record.confidence,
        "importance": record.importance,
        "review_status": record.review_status,
        "tags": record.tags,
        "source_plugin": record.source_plugin,
        "import_batch_id": record.import_batch_id,
        "created_at": record.created_at,
        "created_at_local": _local_time_label(record.created_at),
        "updated_at": record.updated_at,
        "updated_at_local": _local_time_label(record.updated_at),
        "occurred_at": record.occurred_at,
        "occurred_at_local": _local_time_label(record.occurred_at),
        "time_range": {
            "start_at": clean_text(metadata.get("start_at"), 80),
            "end_at": clean_text(metadata.get("end_at"), 80),
            "start_at_local": clean_text(metadata.get("start_at_local"), 80) or _local_time_label(metadata.get("start_at")),
            "end_at_local": clean_text(metadata.get("end_at_local"), 80) or _local_time_label(metadata.get("end_at")),
            "timezone": "Asia/Shanghai",
        },
        "subject": {
            "kind": record.subject.kind,
            "id": record.subject.id,
            "name": record.subject.name,
            "role": record.subject.role,
        },
        "object": {
            "kind": record.object.kind,
            "id": record.object.id,
            "name": record.object.name,
            "role": record.object.role,
        },
    }
    if score is not None:
        data["score"] = score
    if reason:
        data["reason"] = reason
    return data
