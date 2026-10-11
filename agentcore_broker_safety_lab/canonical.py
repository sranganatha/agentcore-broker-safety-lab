"""Canonical action identity used to bind approval and execution."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import math
import re
from typing import Any

from .contracts import ContractError, ErrorCode


def _validate_json(value: Any, path: str = "arguments") -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ContractError(ErrorCode.INVALID_REQUEST, f"{path} contains a non-finite number")
        return
    if isinstance(value, list):
        for index, member in enumerate(value):
            _validate_json(member, f"{path}[{index}]")
        return
    if isinstance(value, Mapping):
        for key, member in value.items():
            if not isinstance(key, str):
                raise ContractError(ErrorCode.INVALID_REQUEST, f"{path} contains a non-string object key")
            _validate_json(member, f"{path}.{key}")
        return
    raise ContractError(ErrorCode.INVALID_REQUEST, f"{path} contains a non-JSON value")


def canonical_arguments(arguments: Mapping[str, Any]) -> str:
    if not isinstance(arguments, Mapping):
        raise ContractError(ErrorCode.INVALID_REQUEST, "arguments must be an object")
    _validate_json(arguments)
    return json.dumps(
        dict(arguments),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def action_digest(task_id: str, tool_name: str, arguments: Mapping[str, Any]) -> str:
    if not isinstance(task_id, str) or not task_id.strip():
        raise ContractError(ErrorCode.INVALID_REQUEST, "task_id must be a non-empty string")
    if not isinstance(tool_name, str) or not tool_name.strip():
        raise ContractError(ErrorCode.INVALID_REQUEST, "tool_name must be a non-empty string")
    action = {
        "arguments": json.loads(canonical_arguments(arguments)),
        "schema_version": 1,
        "task_id": task_id,
        "tool_name": tool_name,
    }
    encoded = json.dumps(action, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def approval_request_id(digest: str) -> str:
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise ContractError(ErrorCode.INVALID_REQUEST, "action digest must be a lowercase SHA-256 digest")
    return f"apr_{digest[:20]}"
