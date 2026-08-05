from __future__ import annotations

"""Structured, privacy-limited DTOs for the Bot Personal archive boundary."""

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any, Mapping

from . import bot_personal_contract
from .bot_personal_contract import (
    BOT_PERSONAL_MEMORY_DOMAIN,
    BOT_PERSONAL_MEMORY_TYPES,
    BOT_PERSONAL_MAX_PAYLOAD_BYTES,
    BOT_PERSONAL_PAYLOAD_SCHEMA_VERSION,
    BOT_PERSONAL_SUBJECT,
    BOT_PERSONAL_WINDOWS,
    TYPE_CONTRACTS,
    WINDOW_SLUGS,
)


BOT_PERSONAL_MAX_RECORD_VERSION = 1_000_000


class BotPersonalValidationError(ValueError):
    def __init__(self, error_code: str, field: str = "", message: str = "") -> None:
        self.error_code = str(error_code or "invalid").strip()[:80]
        self.field = str(field or "").strip()[:160]
        self.message = str(message or self.error_code).strip()[:300]
        super().__init__(self.message)


_FORBIDDEN_KEY_PARTS = (
    "prompt", "conversation", "transcript", "message_chain", "message_history", "raw_message",
    "messages",
    "raw_prompt", "chat_history", "cookie", "token", "secret", "credential", "password",
    "passwd", "authorization", "bearer", "api_key", "apikey", "private_key", "base64",
    "binary", "media_bytes", "image_bytes", "audio_bytes", "video_bytes", "media_binary",
    "media_data", "media_content", "media_blob", "file_path", "filepath", "absolute_path",
    "local_path", "raw_payload",
)
_SENSITIVE_VALUE_PATTERNS = (
    re.compile(r"(?i)^bearer\s+\S+"),
    re.compile(r"(?i)(?:password|passwd|secret|token|api[_-]?key|authorization)\s*[:=]\s*\S+"),
    re.compile(r"(?i)base64,"),
    re.compile(r"(?i)^data:[a-z0-9.+-]+/[a-z0-9.+-]+[;,]"),
    re.compile(r"(?i)^-----begin "),
    re.compile(r"(?i)(?:^|[\s=:;,])(?:[A-Za-z]:[\\/]|\\\\|/home/|/root/|/tmp/|/var/|/volume\d+/)"),
)
_FORBIDDEN_TOP_LEVEL_KEYS = frozenset({
    "conversation", "transcript", "message_chain", "raw_prompt", "raw_payload", "payload_raw",
})


def _key_name(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_")


def _text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _check_string(value: str, path: str) -> str:
    text = _text(value, BOT_PERSONAL_MAX_PAYLOAD_BYTES)
    if any(pattern.search(text) for pattern in _SENSITIVE_VALUE_PATTERNS):
        raise BotPersonalValidationError("privacy_rejected", path, "credential, binary media or absolute path is not accepted")
    return text


def sanitize_bot_personal_value(value: Any, *, path: str = "payload", depth: int = 0) -> Any:
    if depth > 8:
        raise BotPersonalValidationError("privacy_rejected", path, "payload nesting is too deep")
    if isinstance(value, Mapping):
        if len(value) > 64:
            raise BotPersonalValidationError("payload_too_large", path, "too many fields")
        result: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = _text(raw_key, 80)
            normalized = _key_name(key)
            if not key:
                raise BotPersonalValidationError("invalid", path, "empty field name")
            if any(part in normalized for part in _FORBIDDEN_KEY_PARTS):
                raise BotPersonalValidationError("privacy_rejected", f"{path}.{key}", "sensitive or raw field is not accepted")
            result[key] = sanitize_bot_personal_value(raw_value, path=f"{path}.{key}", depth=depth + 1)
        return result
    if isinstance(value, (bytes, bytearray, memoryview)):
        raise BotPersonalValidationError("privacy_rejected", path, "binary media is not accepted")
    if isinstance(value, (list, tuple)):
        if len(value) > 64:
            raise BotPersonalValidationError("payload_too_large", path, "too many list items")
        return [sanitize_bot_personal_value(item, path=f"{path}[{index}]", depth=depth + 1) for index, item in enumerate(value)]
    if isinstance(value, str):
        return _check_string(value, path)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    raise BotPersonalValidationError("invalid", path, "value is not JSON serializable")


def _json_size(value: Any) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError, OverflowError) as exc:
        raise BotPersonalValidationError("invalid", "payload", f"payload is not JSON serializable: {exc}") from exc


def _parse_datetime(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise BotPersonalValidationError("invalid", field, f"{field} must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise BotPersonalValidationError("invalid", field, f"{field} requires a timezone")
    return parsed


def _derive_window(occurred_at: str) -> str:
    hour = _parse_datetime(occurred_at, "occurred_at").hour
    minutes = hour * 60 + _parse_datetime(occurred_at, "occurred_at").minute
    return bot_personal_contract.window_for_minutes(minutes)


def _record_id(memory_type: str, idempotency_key: str) -> str:
    digest = hashlib.sha256(f"{BOT_PERSONAL_MEMORY_DOMAIN}|{memory_type}|{idempotency_key}".encode("utf-8")).hexdigest()[:24]
    return f"botmem_{digest}"


def _fingerprint(data: Mapping[str, Any]) -> str:
    canonical = json.dumps(dict(data), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_bot_personal_idempotency_key(value: Any) -> str:
    if not isinstance(value, str):
        raise BotPersonalValidationError("invalid", "idempotency_key", "idempotency_key must be a string")
    key = _text(value, 240)
    if not key or any(ord(char) < 32 for char in key):
        raise BotPersonalValidationError("invalid", "idempotency_key", "idempotency_key is required and must be printable")
    normalized = _key_name(key)
    if any(part in normalized for part in _FORBIDDEN_KEY_PARTS) or any(pattern.search(key) for pattern in _SENSITIVE_VALUE_PATTERNS):
        raise BotPersonalValidationError("privacy_rejected", "idempotency_key", "unsafe idempotency_key")
    return key


@dataclass(frozen=True)
class BotPersonalArchiveDTO:
    record_id: str
    memory_domain: str
    memory_type: str
    subject: str
    date: str
    window: str
    occurred_at: str
    source_kind: str
    source_refs: tuple[str, ...]
    certainty: float
    evidence_level: str
    status: str
    version: int
    idempotency_key: str
    payload_schema_version: str
    payload: dict[str, Any]

    def envelope(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "memory_domain": self.memory_domain,
            "memory_type": self.memory_type,
            "subject": self.subject,
            "date": self.date,
            "window": self.window,
            "occurred_at": self.occurred_at,
            "source_kind": self.source_kind,
            "source_refs": list(self.source_refs),
            "certainty": self.certainty,
            "evidence_level": self.evidence_level,
            "status": self.status,
            "version": self.version,
            "idempotency_key": self.idempotency_key,
            "payload_schema_version": self.payload_schema_version,
            "payload": deepcopy(self.payload),
        }

    to_dict = envelope


def build_bot_personal_archive(
    envelope: BotPersonalArchiveDTO | Mapping[str, Any] | None = None,
    *,
    memory_type: str | None = None,
    payload: Mapping[str, Any] | None = None,
    record_id: str = "",
    memory_domain: str | None = None,
    subject: str | None = None,
    date: str = "",
    window: str = "",
    occurred_at: str = "",
    source_kind: str = "",
    source_refs: Any = None,
    certainty: Any = None,
    evidence_level: str = "",
    status: str = "",
    version: Any = None,
    idempotency_key: str = "",
    payload_schema_version: str | None = None,
) -> BotPersonalArchiveDTO:
    if isinstance(envelope, BotPersonalArchiveDTO):
        return envelope
    source: dict[str, Any] = dict(envelope or {}) if isinstance(envelope, Mapping) else {}
    if envelope is not None and not isinstance(envelope, (BotPersonalArchiveDTO, Mapping)):
        raise BotPersonalValidationError("invalid", "envelope", "envelope must be an object")
    supplied = {key: value for key, value in {
        "memory_type": memory_type, "payload": payload, "record_id": record_id, "memory_domain": memory_domain,
        "subject": subject, "date": date, "window": window, "occurred_at": occurred_at, "source_kind": source_kind,
        "source_refs": source_refs, "certainty": certainty, "evidence_level": evidence_level, "status": status,
        "version": version, "idempotency_key": idempotency_key, "payload_schema_version": payload_schema_version,
    }.items() if value not in (None, "")}
    source.update(supplied)
    for key in source:
        if _key_name(key) in _FORBIDDEN_TOP_LEVEL_KEYS or any(part in _key_name(key) for part in _FORBIDDEN_KEY_PARTS):
            raise BotPersonalValidationError("privacy_rejected", str(key), "sensitive or raw field is not accepted")

    kind = _text(source.get("memory_type"), 80).lower()
    if kind not in BOT_PERSONAL_MEMORY_TYPES or kind not in TYPE_CONTRACTS:
        raise BotPersonalValidationError("invalid", "memory_type", "unknown Bot Personal memory type")
    if _text(source.get("memory_domain"), 80) not in ("", BOT_PERSONAL_MEMORY_DOMAIN):
        raise BotPersonalValidationError("invalid", "memory_domain", "Bot Personal domain is fixed")
    if _text(source.get("subject"), 80) not in ("", BOT_PERSONAL_SUBJECT):
        raise BotPersonalValidationError("invalid", "subject", "Bot Personal subject is fixed")

    key = validate_bot_personal_idempotency_key(source.get("idempotency_key"))
    derived_record_id = _record_id(kind, key)
    supplied_record_id = source.get("record_id")
    if supplied_record_id not in (None, ""):
        if not isinstance(supplied_record_id, str):
            raise BotPersonalValidationError("invalid", "record_id", "record_id must be a string")
        record_id = _check_string(_text(supplied_record_id, 120), "record_id")
        if record_id != derived_record_id:
            raise BotPersonalValidationError("invalid", "record_id", "record_id must match the idempotency key")
    raw_payload = source.get("payload")
    if not isinstance(raw_payload, Mapping):
        raise BotPersonalValidationError("invalid", "payload", "payload must be an object")
    safe_payload = sanitize_bot_personal_value(raw_payload, path="payload")
    if _json_size(safe_payload) > BOT_PERSONAL_MAX_PAYLOAD_BYTES:
        raise BotPersonalValidationError("payload_too_large", "payload", "payload exceeds the contract limit")

    occurred = _text(source.get("occurred_at"), 80)
    if not occurred:
        occurred = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _parse_datetime(occurred, "occurred_at")
    record_date = _text(source.get("date"), 20) or occurred[:10]
    try:
        datetime.strptime(record_date, "%Y-%m-%d")
    except ValueError as exc:
        raise BotPersonalValidationError("invalid", "date", "date must be YYYY-MM-DD") from exc
    record_window = _text(source.get("window"), 40).lower() or _derive_window(occurred)
    if record_window not in BOT_PERSONAL_WINDOWS or record_window not in WINDOW_SLUGS:
        raise BotPersonalValidationError("invalid", "window", "window is outside the shared contract")
    try:
        confidence = float(source.get("certainty", 0.6))
    except (TypeError, ValueError) as exc:
        raise BotPersonalValidationError("invalid", "certainty", "certainty must be numeric") from exc
    if not 0.0 <= confidence <= 1.0:
        raise BotPersonalValidationError("invalid", "certainty", "certainty must be between 0 and 1")
    try:
        record_version = int(source.get("version", 1))
    except (TypeError, ValueError) as exc:
        raise BotPersonalValidationError("invalid", "version", "version must be a positive integer") from exc
    if not 1 <= record_version <= BOT_PERSONAL_MAX_RECORD_VERSION:
        raise BotPersonalValidationError(
            "invalid",
            "version",
            f"version must be between 1 and {BOT_PERSONAL_MAX_RECORD_VERSION}",
        )
    schema = _text(source.get("payload_schema_version"), 40) or BOT_PERSONAL_PAYLOAD_SCHEMA_VERSION
    if schema != BOT_PERSONAL_PAYLOAD_SCHEMA_VERSION:
        raise BotPersonalValidationError("invalid", "payload_schema_version", "unsupported payload schema")
    refs_value = source.get("source_refs")
    if refs_value is None:
        refs_value = [f"archive:{key}"]
    if not isinstance(refs_value, (list, tuple)):
        raise BotPersonalValidationError("invalid", "source_refs", "source_refs must be a list")
    refs_list: list[str] = []
    for item in refs_value:
        if not isinstance(item, str):
            raise BotPersonalValidationError("invalid", "source_refs", "source_refs must contain strings")
        reference = _check_string(_text(item, 240), "source_refs")
        if reference:
            refs_list.append(reference)
    refs = tuple(refs_list)
    if not refs:
        raise BotPersonalValidationError("invalid", "source_refs", "source_refs must not be empty")
    contract_source, contract_evidence, contract_status = TYPE_CONTRACTS[kind]
    return BotPersonalArchiveDTO(
        record_id=derived_record_id,
        memory_domain=BOT_PERSONAL_MEMORY_DOMAIN,
        memory_type=kind,
        subject=BOT_PERSONAL_SUBJECT,
        date=record_date,
        window=record_window,
        occurred_at=occurred,
        source_kind=contract_source,
        source_refs=refs,
        certainty=confidence,
        evidence_level=contract_evidence,
        status=contract_status,
        version=record_version,
        idempotency_key=key,
        payload_schema_version=schema,
        payload=deepcopy(safe_payload),
    )


def bot_personal_payload_fingerprint(dto: BotPersonalArchiveDTO) -> str:
    payload = dto.envelope()
    payload.pop("record_id", None)
    return _fingerprint(payload)


__all__ = [
    "BOT_PERSONAL_MAX_RECORD_VERSION", "BotPersonalArchiveDTO", "BotPersonalValidationError", "build_bot_personal_archive",
    "bot_personal_payload_fingerprint", "sanitize_bot_personal_value", "validate_bot_personal_idempotency_key",
]
