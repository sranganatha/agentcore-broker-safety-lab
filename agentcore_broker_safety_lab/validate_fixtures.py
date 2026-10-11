"""Validate bundled tool and synthetic-domain fixtures without external services."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any

from .contracts import BrokerTask, ContractError, ErrorCode


ROOT = Path(__file__).resolve().parent.parent
EXPECTED_TOOLS = {"get_runbook_procedure", "inspect_service", "execute_remediation"}


def _load_json(name: str) -> Any:
    path = ROOT / "config" / name
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(ErrorCode.INVALID_REQUEST, f"cannot load {name}") from exc


def _validate_schema(schema: Any, label: str) -> None:
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{label} must be an object schema")
    properties = schema.get("properties")
    required = schema.get("required", [])
    if not isinstance(properties, dict) or not properties:
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{label} must define properties")
    if not isinstance(required, list) or not set(required).issubset(properties):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{label} has invalid required properties")
    if schema.get("additionalProperties") is not False:
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{label} must reject additional properties")


def validate_tools() -> None:
    tools = _load_json("tools.json")
    if not isinstance(tools, list):
        raise ContractError(ErrorCode.INVALID_REQUEST, "tools.json must contain a list")
    names = [tool.get("name") for tool in tools if isinstance(tool, dict)]
    if len(tools) != 3 or set(names) != EXPECTED_TOOLS or len(names) != len(set(names)):
        raise ContractError(ErrorCode.INVALID_REQUEST, "tools.json must define the three unique lab tools")
    for tool in tools:
        if set(tool) != {"name", "description", "inputSchema", "outputSchema"}:
            raise ContractError(ErrorCode.INVALID_REQUEST, f"tool {tool.get('name')} has unexpected fields")
        if not isinstance(tool["description"], str) or not tool["description"].strip():
            raise ContractError(ErrorCode.INVALID_REQUEST, f"tool {tool['name']} needs a description")
        _validate_schema(tool["inputSchema"], f"{tool['name']} inputSchema")
        _validate_schema(tool["outputSchema"], f"{tool['name']} outputSchema")


def validate_runbooks() -> dict[tuple[str, str], tuple[str, str]]:
    runbooks = _load_json("synthetic_runbooks.json")
    if not isinstance(runbooks, list) or len(runbooks) != 2:
        raise ContractError(ErrorCode.INVALID_REQUEST, "synthetic_runbooks.json must contain two runbooks")
    identities: dict[tuple[str, str], tuple[str, str]] = {}
    for runbook in runbooks:
        expected = {"runbook_id", "service_id", "alarm_code", "allowed_action", "procedure", "source_digest"}
        if not isinstance(runbook, dict) or set(runbook) != expected:
            raise ContractError(ErrorCode.INVALID_REQUEST, "runbook fields do not match the contract")
        identity = (runbook["service_id"], runbook["alarm_code"])
        if identity in identities:
            raise ContractError(ErrorCode.INVALID_REQUEST, "runbook service and alarm identity must be unique")
        identities[identity] = (runbook["runbook_id"], runbook["allowed_action"])
        digest = hashlib.sha256(runbook["procedure"].encode("utf-8")).hexdigest()
        if digest != runbook["source_digest"] or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise ContractError(ErrorCode.INVALID_REQUEST, f"runbook {runbook['runbook_id']} digest does not match")
    return identities


def validate_cases(runbook_identities: dict[tuple[str, str], tuple[str, str]]) -> None:
    cases = _load_json("cases.json")
    if not isinstance(cases, list) or len(cases) != 2:
        raise ContractError(ErrorCode.INVALID_REQUEST, "cases.json must contain two cases")
    case_ids: set[str] = set()
    for case in cases:
        if not isinstance(case, dict) or set(case) != {"case_id", "task", "expected_runbook", "expected_action"}:
            raise ContractError(ErrorCode.INVALID_REQUEST, "case fields do not match the contract")
        if case["case_id"] in case_ids:
            raise ContractError(ErrorCode.INVALID_REQUEST, "case IDs must be unique")
        case_ids.add(case["case_id"])
        task = BrokerTask.from_mapping(case["task"])
        identity = (task.service_id, task.alarm_code)
        if identity not in runbook_identities:
            raise ContractError(ErrorCode.INVALID_REQUEST, f"case {case['case_id']} has no matching runbook")
        expected_runbook, expected_action = runbook_identities[identity]
        if case["expected_runbook"] != expected_runbook or case["expected_action"] != expected_action:
            raise ContractError(ErrorCode.INVALID_REQUEST, f"case {case['case_id']} expectation does not match its runbook")


def validate_all() -> None:
    validate_tools()
    runbooks = validate_runbooks()
    validate_cases(runbooks)


if __name__ == "__main__":
    validate_all()
    print("validated 3 tools, 2 synthetic runbooks, and 2 broker cases")
