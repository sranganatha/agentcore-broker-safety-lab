"""Validated AgentCore Lambda target for the three broker tools."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
import json
from typing import Any

from .canonical import action_digest, approval_request_id
from .contracts import ApprovalRequest, ApprovalStatus, ContractError, ErrorCode, ExecutionReceipt
from .ledger import InMemoryLedger, Ledger
from .validate_fixtures import ROOT, validate_runtime_fixtures


TOOL_DELIMITER = "___"
TOOLS = {"get_runbook_procedure", "inspect_service", "execute_remediation"}
EXECUTE_TOOL = "execute_remediation"
APPROVAL_TTL = timedelta(minutes=5)


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{field_name} must be a non-empty string")
    if len(value) > 128:
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{field_name} exceeds 128 characters")
    return value


def _arguments(values: Any, expected: set[str]) -> dict[str, str]:
    if not isinstance(values, Mapping) or set(values) != expected:
        raise ContractError(ErrorCode.INVALID_REQUEST, "tool arguments do not match the contract")
    return {name: _required_text(values[name], name) for name in expected}


def _tool_name(context: Any) -> str:
    client_context = getattr(context, "client_context", None)
    custom = getattr(client_context, "custom", None)
    if not isinstance(custom, Mapping):
        raise ContractError(ErrorCode.INVALID_REQUEST, "AgentCore Lambda context is missing")
    visible_name = custom.get("bedrockAgentCoreToolName")
    if not isinstance(visible_name, str):
        raise ContractError(ErrorCode.INVALID_REQUEST, "AgentCore tool name is missing")
    target_name, separator, tool_name = visible_name.partition(TOOL_DELIMITER)
    if not separator or not target_name or TOOL_DELIMITER in tool_name or tool_name not in TOOLS:
        raise ContractError(ErrorCode.UNKNOWN_TOOL, "tool name is not in the configured target")
    return tool_name


def _receipt_response(receipt: ExecutionReceipt) -> dict[str, Any]:
    return {
        "status": receipt.status.value,
        "execution_id": receipt.execution_id,
        "effect_count": receipt.effect_count,
    }


class BrokerToolTarget:
    def __init__(self, ledger: Ledger | None = None) -> None:
        validate_runtime_fixtures()
        self.ledger = ledger or InMemoryLedger()
        self._runbooks = self._load_fixture("synthetic_runbooks.json", "runbook")
        self._services = self._load_fixture("synthetic_services.json", "service")

    @staticmethod
    def _load_fixture(name: str, label: str) -> list[dict[str, str]]:
        try:
            values = json.loads((ROOT / "config" / name).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ContractError(ErrorCode.INVALID_REQUEST, f"cannot load synthetic {label} fixtures") from exc
        return values

    def handle(
        self,
        event: Mapping[str, Any],
        context: Any,
        *,
        now: datetime | None = None,
        lose_response_after_commit: bool = False,
    ) -> dict[str, Any]:
        tool_name = _tool_name(context)
        current_time = now or datetime.now(UTC)
        if tool_name == "get_runbook_procedure":
            return self._get_runbook(event)
        if tool_name == "inspect_service":
            return self._inspect_service(event)
        return self._execute(event, current_time, lose_response_after_commit)

    def _get_runbook(self, event: Mapping[str, Any]) -> dict[str, Any]:
        arguments = _arguments(event, {"service_id", "alarm_code"})
        for runbook in self._runbooks:
            if (
                runbook["service_id"] == arguments["service_id"]
                and runbook["alarm_code"] == arguments["alarm_code"]
            ):
                return {
                    "runbook_id": runbook["runbook_id"],
                    "procedure": runbook["procedure"],
                    "allowed_action": runbook["allowed_action"],
                    "source_digest": runbook["source_digest"],
                }
        raise ContractError(ErrorCode.INVALID_REQUEST, "no runbook matches the requested service alarm")

    def _inspect_service(self, event: Mapping[str, Any]) -> dict[str, Any]:
        arguments = _arguments(event, {"service_id", "environment"})
        for service in self._services:
            if (
                service["service_id"] == arguments["service_id"]
                and service["environment"] == arguments["environment"]
            ):
                return dict(service)
        raise ContractError(ErrorCode.INVALID_REQUEST, "synthetic service was not found")

    def _execute(
        self,
        event: Mapping[str, Any],
        now: datetime,
        lose_response_after_commit: bool,
    ) -> dict[str, Any]:
        arguments = _arguments(
            event,
            {"task_id", "service_id", "environment", "action", "idempotency_key"},
        )
        self._validate_action(arguments)
        digest = action_digest(arguments["task_id"], EXECUTE_TOOL, arguments)
        request = ApprovalRequest(
            approval_request_id=approval_request_id(digest),
            task_id=arguments["task_id"],
            tool_name=EXECUTE_TOOL,
            action_digest=digest,
            action_summary=(
                f"{arguments['action']} for {arguments['service_id']} in {arguments['environment']}"
            ),
            expires_at=now + APPROVAL_TTL,
            status=ApprovalStatus.PENDING,
        )
        approval = self.ledger.ensure_approval(request, now)
        if approval.status is ApprovalStatus.PENDING:
            return {
                "status": ErrorCode.APPROVAL_REQUIRED.value,
                "approval_request_id": approval.approval_request_id,
            }
        if approval.status is ApprovalStatus.EXPIRED or approval.expires_at <= now:
            raise ContractError(ErrorCode.APPROVAL_EXPIRED, "approval has expired")
        receipt = self.ledger.commit(
            task_id=arguments["task_id"],
            tool_name=EXECUTE_TOOL,
            approval_request_id=approval.approval_request_id,
            action_digest=digest,
            idempotency_key=arguments["idempotency_key"],
            now=now,
        )
        if lose_response_after_commit:
            raise ContractError(ErrorCode.OUTCOME_UNKNOWN, "response was lost after execution commit")
        return _receipt_response(receipt)

    def approve(
        self,
        task_id: str,
        approval_request_id_value: str,
        *,
        now: datetime | None = None,
    ) -> ApprovalRequest:
        return self.ledger.approve(
            _required_text(task_id, "task_id"),
            EXECUTE_TOOL,
            _required_text(approval_request_id_value, "approval_request_id"),
            now or datetime.now(UTC),
        )

    def reconcile_execution(
        self,
        event: Mapping[str, Any],
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        arguments = _arguments(
            event,
            {"task_id", "service_id", "environment", "action", "idempotency_key"},
        )
        self._validate_action(arguments)
        digest = action_digest(arguments["task_id"], EXECUTE_TOOL, arguments)
        receipt = self.ledger.reconcile(
            task_id=arguments["task_id"],
            tool_name=EXECUTE_TOOL,
            action_digest=digest,
            idempotency_key=arguments["idempotency_key"],
            now=now or datetime.now(UTC),
        )
        return _receipt_response(receipt)

    def _validate_action(self, arguments: Mapping[str, str]) -> None:
        allowed = any(
            service["service_id"] == arguments["service_id"]
            and service["environment"] == arguments["environment"]
            and service["active_alarm"] == "HIGH_ERROR_RATE"
            for service in self._services
        )
        if not allowed or arguments["action"] != "restart_worker_pool":
            raise ContractError(ErrorCode.ACTION_NOT_ALLOWED, "synthetic remediation is not allowed")
