"""Validated domain contracts for broker tasks, approvals, and receipts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import re
from typing import Any, Self


class ErrorCode(StrEnum):
    INVALID_REQUEST = "INVALID_REQUEST"
    UNKNOWN_TOOL = "UNKNOWN_TOOL"
    POLICY_DENIED = "POLICY_DENIED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    APPROVAL_EXPIRED = "APPROVAL_EXPIRED"
    APPROVAL_ARGUMENT_MISMATCH = "APPROVAL_ARGUMENT_MISMATCH"
    APPROVAL_ALREADY_CONSUMED = "APPROVAL_ALREADY_CONSUMED"
    ACTION_NOT_ALLOWED = "ACTION_NOT_ALLOWED"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"
    RECONCILIATION_FAILED = "RECONCILIATION_FAILED"
    PROVIDER_FAILURE = "PROVIDER_FAILURE"


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    CONSUMED = "CONSUMED"
    EXPIRED = "EXPIRED"


class ExecutionStatus(StrEnum):
    COMMITTED = "COMMITTED"
    REJECTED = "REJECTED"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"


class ContractError(ValueError):
    """A safe boundary error with a stable machine-readable code."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{field_name} must be a non-empty string")
    return value


def _aware_datetime(value: Any, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{field_name} must be an ISO-8601 string")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{field_name} must be an ISO-8601 string") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{field_name} must include a timezone")
    return parsed


def _require_aware_datetime(value: Any, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{field_name} must be a timezone-aware datetime")
    return value


def _action_digest(value: Any) -> str:
    digest = _required_text(value, "action_digest")
    if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise ContractError(ErrorCode.INVALID_REQUEST, "action_digest must be a lowercase SHA-256 digest")
    return digest


def _exact_fields(values: Mapping[str, Any], expected: set[str]) -> None:
    actual = set(values)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"contract fields do not match; missing={missing}, extra={extra}",
        )


@dataclass(frozen=True, slots=True)
class BrokerTask:
    task_id: str
    service_id: str
    environment: str
    alarm_code: str
    requested_outcome: str
    correlation_id: str

    def __post_init__(self) -> None:
        for field_name in (
            "task_id",
            "service_id",
            "environment",
            "alarm_code",
            "requested_outcome",
            "correlation_id",
        ):
            _required_text(getattr(self, field_name), field_name)

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> Self:
        fields = {
            "task_id",
            "service_id",
            "environment",
            "alarm_code",
            "requested_outcome",
            "correlation_id",
        }
        _exact_fields(values, fields)
        return cls(**{field: _required_text(values[field], field) for field in fields})


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    approval_request_id: str
    task_id: str
    tool_name: str
    action_digest: str
    action_summary: str
    expires_at: datetime
    status: ApprovalStatus

    def __post_init__(self) -> None:
        _required_text(self.approval_request_id, "approval_request_id")
        _required_text(self.task_id, "task_id")
        _required_text(self.tool_name, "tool_name")
        _action_digest(self.action_digest)
        _required_text(self.action_summary, "action_summary")
        _require_aware_datetime(self.expires_at, "expires_at")
        if not isinstance(self.status, ApprovalStatus):
            raise ContractError(ErrorCode.INVALID_REQUEST, "status is not a valid approval state")

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> Self:
        fields = {
            "approval_request_id",
            "task_id",
            "tool_name",
            "action_digest",
            "action_summary",
            "expires_at",
            "status",
        }
        _exact_fields(values, fields)
        try:
            status = ApprovalStatus(values["status"])
        except (TypeError, ValueError) as exc:
            raise ContractError(ErrorCode.INVALID_REQUEST, "status is not a valid approval state") from exc
        return cls(
            approval_request_id=_required_text(values["approval_request_id"], "approval_request_id"),
            task_id=_required_text(values["task_id"], "task_id"),
            tool_name=_required_text(values["tool_name"], "tool_name"),
            action_digest=_action_digest(values["action_digest"]),
            action_summary=_required_text(values["action_summary"], "action_summary"),
            expires_at=_aware_datetime(values["expires_at"], "expires_at"),
            status=status,
        )


@dataclass(frozen=True, slots=True)
class ExecutionReceipt:
    execution_id: str
    idempotency_key: str
    task_id: str
    action_digest: str
    status: ExecutionStatus
    effect_count: int | None
    provider_operation_id: str | None
    created_at: datetime
    reconciled_at: datetime | None

    def __post_init__(self) -> None:
        _required_text(self.execution_id, "execution_id")
        _required_text(self.idempotency_key, "idempotency_key")
        _required_text(self.task_id, "task_id")
        _action_digest(self.action_digest)
        _require_aware_datetime(self.created_at, "created_at")
        if self.reconciled_at is not None:
            _require_aware_datetime(self.reconciled_at, "reconciled_at")
            if self.reconciled_at < self.created_at:
                raise ContractError(ErrorCode.INVALID_REQUEST, "reconciled_at cannot precede created_at")
        if not isinstance(self.status, ExecutionStatus):
            raise ContractError(ErrorCode.INVALID_REQUEST, "status is not a valid execution state")
        if self.effect_count is not None and type(self.effect_count) is not int:
            raise ContractError(ErrorCode.INVALID_REQUEST, "effect_count must be an integer or null")
        if self.provider_operation_id is not None:
            _required_text(self.provider_operation_id, "provider_operation_id")
        if self.status is ExecutionStatus.COMMITTED:
            if self.effect_count != 1 or not self.provider_operation_id:
                raise ContractError(
                    ErrorCode.INVALID_REQUEST,
                    "a committed receipt requires one effect and a provider operation ID",
                )
        elif self.status is ExecutionStatus.REJECTED:
            if self.effect_count != 0 or self.provider_operation_id is not None:
                raise ContractError(
                    ErrorCode.INVALID_REQUEST,
                    "a rejected receipt requires zero effects and no provider operation ID",
                )
        elif self.status is ExecutionStatus.OUTCOME_UNKNOWN:
            if self.effect_count is not None:
                raise ContractError(
                    ErrorCode.INVALID_REQUEST,
                    "an unknown outcome cannot claim an effect count",
                )
