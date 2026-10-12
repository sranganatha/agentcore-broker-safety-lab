"""Strict broker workflow around untrusted model output."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Callable


MODEL_ID = "amazon.nova-micro-v1:0"
MAX_RESPONSE_BYTES = 8_192
_FIELDS = {"task_id", "service_id", "alarm_code", "environment"}
_VALUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class BrokerInputError(ValueError):
    pass


class BrokerTransportError(RuntimeError):
    pass


class BrokerTimeoutError(RuntimeError):
    pass


class BrokerTruncatedError(RuntimeError):
    pass


@dataclass(frozen=True)
class Evidence:
    health: str
    active_alarm: str
    runbook_id: str
    source_digest: str
    allowed_action: str


def parse_evidence(
    request: dict[str, str], runbook: Any, inspection: Any
) -> Evidence:
    runbook_fields = {"runbook_id", "procedure", "allowed_action", "source_digest"}
    inspection_fields = {"service_id", "environment", "health", "active_alarm"}
    if not isinstance(runbook, dict) or set(runbook) != runbook_fields:
        raise BrokerTransportError("runbook evidence has an invalid shape")
    if not isinstance(inspection, dict) or set(inspection) != inspection_fields:
        raise BrokerTransportError("inspection evidence has an invalid shape")
    if any(not isinstance(value, str) for value in (*runbook.values(), *inspection.values())):
        raise BrokerTransportError("tool evidence values must be strings")
    if inspection["service_id"] != request["service_id"] or inspection["environment"] != request["environment"]:
        raise BrokerTransportError("inspection evidence changed the task")
    if runbook["allowed_action"] not in {"restart_worker_pool", "escalate_only"}:
        raise BrokerTransportError("runbook evidence contains an unsupported action")
    if not _DIGEST.fullmatch(runbook["source_digest"]):
        raise BrokerTransportError("runbook evidence contains an invalid digest")
    return Evidence(
        health=inspection["health"],
        active_alarm=inspection["active_alarm"],
        runbook_id=runbook["runbook_id"],
        source_digest=runbook["source_digest"],
        allowed_action=runbook["allowed_action"],
    )


def validate_request(payload: Any) -> dict[str, str]:
    if not isinstance(payload, dict) or set(payload) != _FIELDS:
        raise BrokerInputError("request must contain exactly task_id, service_id, alarm_code, environment")
    if any(not isinstance(value, str) or not _VALUE.fullmatch(value) for value in payload.values()):
        raise BrokerInputError("request values must be bounded identifiers")
    return dict(payload)


def proposal_prompt(request: dict[str, str], evidence: Evidence) -> str:
    return json.dumps(
        {
            "task": request,
            "evidence": {
                "health": evidence.health,
                "active_alarm": evidence.active_alarm,
                "runbook_id": evidence.runbook_id,
                "source_digest": evidence.source_digest,
            },
            "allowed_action": evidence.allowed_action,
            "instruction": "Return only the required proposal JSON. Do not claim execution or approval.",
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def validate_proposal(raw: str, request: dict[str, str], evidence: Evidence) -> dict[str, Any]:
    if len(raw.encode("utf-8")) > MAX_RESPONSE_BYTES:
        raise BrokerTruncatedError("model response exceeded the byte limit")
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError("model response is not JSON") from exc
    expected = {"status", "service_id", "alarm_code", "environment", "evidence", "proposed_action", "requires_approval"}
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("model response has an invalid shape")
    expected_evidence = {
        "health": evidence.health,
        "active_alarm": evidence.active_alarm,
        "runbook_id": evidence.runbook_id,
        "source_digest": evidence.source_digest,
    }
    if value["evidence"] != expected_evidence:
        raise ValueError("model response changed the evidence")
    if any(value[key] != request[key] for key in ("service_id", "alarm_code", "environment")):
        raise ValueError("model response changed the task")
    if value["status"] not in {"PROPOSED", "INSUFFICIENT_EVIDENCE"}:
        raise ValueError("model response status is invalid")
    if value["requires_approval"] is not True:
        raise ValueError("model response bypassed approval")
    if value["status"] == "PROPOSED" and value["proposed_action"] != evidence.allowed_action:
        raise ValueError("model response proposed an unsupported action")
    if value["status"] == "INSUFFICIENT_EVIDENCE" and value["proposed_action"] is not None:
        raise ValueError("insufficient evidence cannot propose an action")
    return value


def run_broker(
    payload: Any,
    read_evidence: Callable[[dict[str, str]], Evidence],
    generate: Callable[[str], tuple[str, str]],
) -> dict[str, Any]:
    read_tool_calls = 0
    try:
        request = validate_request(payload)
    except BrokerInputError:
        return _result("INVALID_INPUT")
    try:
        evidence = read_evidence(request)
        read_tool_calls = 2
        raw, stop_reason = generate(proposal_prompt(request, evidence))
        if stop_reason == "cancelled":
            return _result("TIMED_OUT", read_tool_calls=2, stop_reason=stop_reason)
        if stop_reason in {"guardrail_intervened", "content_filtered", "refusal"}:
            return _result("REFUSED", read_tool_calls=2, stop_reason=stop_reason)
        if stop_reason in {"limit_turns", "limit_output_tokens", "limit_total_tokens"}:
            return _result("BUDGET_EXCEEDED", read_tool_calls=2, stop_reason=stop_reason)
        if stop_reason in {"max_tokens", "length"}:
            return _result("TRUNCATED", read_tool_calls=2, stop_reason=stop_reason)
        proposal = validate_proposal(raw, request, evidence)
    except BrokerTimeoutError:
        return _result("TIMED_OUT", read_tool_calls=read_tool_calls)
    except BrokerTruncatedError:
        return _result("TRUNCATED", read_tool_calls=read_tool_calls)
    except BrokerTransportError:
        return _result("TRANSPORT_FAILURE", read_tool_calls=read_tool_calls)
    except ValueError as exc:
        return _result("INVALID_OUTPUT", read_tool_calls=read_tool_calls, reason=str(exc))
    return _result("COMPLETED", read_tool_calls=2, stop_reason=stop_reason, proposal=proposal)


def _result(
    outcome: str,
    *,
    read_tool_calls: int = 0,
    stop_reason: str | None = None,
    proposal: dict[str, Any] | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "outcome": outcome,
        "model": MODEL_ID,
        "read_tool_calls": read_tool_calls,
        "write_tool_calls": 0,
    }
    if stop_reason:
        result["stop_reason"] = stop_reason
    if reason:
        result["reason"] = reason
    if proposal is not None:
        result["proposal"] = proposal
    return result
