"""Approval and execution ledgers for the synthetic broker target."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
import hashlib
from threading import Lock
from typing import Any, Protocol

from .contracts import (
    ApprovalRequest,
    ApprovalStatus,
    ContractError,
    ErrorCode,
    ExecutionReceipt,
    ExecutionStatus,
)


class Ledger(Protocol):
    def ensure_approval(self, request: ApprovalRequest, now: datetime) -> ApprovalRequest: ...

    def approve(
        self,
        task_id: str,
        tool_name: str,
        approval_request_id: str,
        now: datetime,
    ) -> ApprovalRequest: ...

    def commit(
        self,
        task_id: str,
        tool_name: str,
        approval_request_id: str,
        action_digest: str,
        idempotency_key: str,
        now: datetime,
    ) -> ExecutionReceipt: ...

    def reconcile(
        self,
        task_id: str,
        tool_name: str,
        action_digest: str,
        idempotency_key: str,
        now: datetime,
    ) -> ExecutionReceipt: ...


def _execution_receipt(
    task_id: str,
    action_digest: str,
    idempotency_key: str,
    now: datetime,
) -> ExecutionReceipt:
    identity = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:20]
    return ExecutionReceipt(
        execution_id=f"exec_{identity}",
        idempotency_key=idempotency_key,
        task_id=task_id,
        action_digest=action_digest,
        status=ExecutionStatus.COMMITTED,
        effect_count=1,
        provider_operation_id=f"synthetic_{identity}",
        created_at=now,
        reconciled_at=None,
    )


def _validate_receipt(
    receipt: ExecutionReceipt,
    task_id: str,
    action_digest: str,
    idempotency_key: str,
) -> ExecutionReceipt:
    if (
        receipt.task_id != task_id
        or receipt.action_digest != action_digest
        or receipt.idempotency_key != idempotency_key
    ):
        raise ContractError(
            ErrorCode.APPROVAL_ARGUMENT_MISMATCH,
            "execution receipt is bound to a different action",
        )
    return receipt


def _validate_approval(
    approval: ApprovalRequest | None,
    approval_request_id: str,
    action_digest: str,
    now: datetime,
) -> ApprovalRequest:
    if approval is None:
        raise ContractError(ErrorCode.APPROVAL_REQUIRED, "approval is required")
    if approval.approval_request_id != approval_request_id or approval.action_digest != action_digest:
        raise ContractError(ErrorCode.APPROVAL_ARGUMENT_MISMATCH, "approval does not match the action")
    if approval.expires_at <= now or approval.status is ApprovalStatus.EXPIRED:
        raise ContractError(ErrorCode.APPROVAL_EXPIRED, "approval has expired")
    if approval.status is ApprovalStatus.PENDING:
        raise ContractError(ErrorCode.APPROVAL_REQUIRED, "approval is required")
    if approval.status is ApprovalStatus.CONSUMED:
        raise ContractError(ErrorCode.APPROVAL_ALREADY_CONSUMED, "approval has already been consumed")
    return approval


class InMemoryLedger:
    """Thread-safe ledger used by account-free tests and demonstrations."""

    def __init__(self) -> None:
        self._approvals: dict[tuple[str, str], ApprovalRequest] = {}
        self._receipts: dict[tuple[str, str], ExecutionReceipt] = {}
        self._lock = Lock()

    @property
    def effect_count(self) -> int:
        with self._lock:
            return len(self._receipts)

    def ensure_approval(self, request: ApprovalRequest, now: datetime) -> ApprovalRequest:
        key = (request.task_id, request.tool_name)
        with self._lock:
            existing = self._approvals.get(key)
            if existing is None:
                self._approvals[key] = request
                return request
            if existing.action_digest != request.action_digest:
                raise ContractError(
                    ErrorCode.APPROVAL_ARGUMENT_MISMATCH,
                    "task already has an approval request for different arguments",
                )
            if existing.expires_at <= now and existing.status is not ApprovalStatus.CONSUMED:
                existing = replace(existing, status=ApprovalStatus.EXPIRED)
                self._approvals[key] = existing
            return existing

    def approve(
        self,
        task_id: str,
        tool_name: str,
        approval_request_id: str,
        now: datetime,
    ) -> ApprovalRequest:
        key = (task_id, tool_name)
        with self._lock:
            approval = self._approvals.get(key)
            if approval is None or approval.approval_request_id != approval_request_id:
                raise ContractError(ErrorCode.APPROVAL_ARGUMENT_MISMATCH, "approval request does not match")
            if approval.expires_at <= now or approval.status is ApprovalStatus.EXPIRED:
                self._approvals[key] = replace(approval, status=ApprovalStatus.EXPIRED)
                raise ContractError(ErrorCode.APPROVAL_EXPIRED, "approval has expired")
            if approval.status is ApprovalStatus.CONSUMED:
                raise ContractError(ErrorCode.APPROVAL_ALREADY_CONSUMED, "approval has already been consumed")
            if approval.status is ApprovalStatus.PENDING:
                approval = replace(approval, status=ApprovalStatus.APPROVED)
                self._approvals[key] = approval
            return approval

    def commit(
        self,
        task_id: str,
        tool_name: str,
        approval_request_id: str,
        action_digest: str,
        idempotency_key: str,
        now: datetime,
    ) -> ExecutionReceipt:
        key = (task_id, tool_name)
        with self._lock:
            existing = self._receipts.get(key)
            if existing is not None:
                return _validate_receipt(existing, task_id, action_digest, idempotency_key)
            approval = _validate_approval(
                self._approvals.get(key),
                approval_request_id,
                action_digest,
                now,
            )
            receipt = _execution_receipt(task_id, action_digest, idempotency_key, now)
            self._receipts[key] = receipt
            self._approvals[key] = replace(approval, status=ApprovalStatus.CONSUMED)
            return receipt

    def reconcile(
        self,
        task_id: str,
        tool_name: str,
        action_digest: str,
        idempotency_key: str,
        now: datetime,
    ) -> ExecutionReceipt:
        key = (task_id, tool_name)
        with self._lock:
            receipt = self._receipts.get(key)
            if receipt is None:
                raise ContractError(ErrorCode.RECONCILIATION_FAILED, "no execution receipt was found")
            _validate_receipt(receipt, task_id, action_digest, idempotency_key)
            if receipt.reconciled_at is None:
                receipt = replace(receipt, reconciled_at=now)
                self._receipts[key] = receipt
            return receipt


def _iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ContractError(ErrorCode.INVALID_REQUEST, "ledger time must include a timezone")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _key(task_id: str, tool_name: str) -> dict[str, str]:
    return {"record_key": f"APPROVAL#{task_id}#{tool_name}"}


def _approval_item(request: ApprovalRequest) -> dict[str, Any]:
    return {
        **_key(request.task_id, request.tool_name),
        "approval_request_id": request.approval_request_id,
        "task_id": request.task_id,
        "tool_name": request.tool_name,
        "action_digest": request.action_digest,
        "action_summary": request.action_summary,
        "expires_at": _iso(request.expires_at),
        "status": request.status.value,
    }


def _read_approval(item: Mapping[str, Any] | None) -> ApprovalRequest | None:
    if not item:
        return None
    try:
        return ApprovalRequest(
            approval_request_id=item["approval_request_id"],
            task_id=item["task_id"],
            tool_name=item["tool_name"],
            action_digest=item["action_digest"],
            action_summary=item["action_summary"],
            expires_at=_datetime(item["expires_at"]),
            status=ApprovalStatus(item["status"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ContractError(ErrorCode.PROVIDER_FAILURE, "approval record is invalid") from exc


def _read_receipt(item: Mapping[str, Any] | None) -> ExecutionReceipt | None:
    if not item or "execution_id" not in item:
        return None
    try:
        reconciled_at = item.get("reconciled_at")
        effect_count = item["effect_count"]
        if type(effect_count) is not int:
            normalized_effect_count = int(effect_count)
            if isinstance(effect_count, bool) or normalized_effect_count != effect_count:
                raise ValueError("effect_count is not an integer")
            effect_count = normalized_effect_count
        return ExecutionReceipt(
            execution_id=item["execution_id"],
            idempotency_key=item["idempotency_key"],
            task_id=item["task_id"],
            action_digest=item["action_digest"],
            status=ExecutionStatus.COMMITTED,
            effect_count=effect_count,
            provider_operation_id=item["provider_operation_id"],
            created_at=_datetime(item["created_at"]),
            reconciled_at=_datetime(reconciled_at) if reconciled_at else None,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ContractError(ErrorCode.PROVIDER_FAILURE, "execution receipt is invalid") from exc


def _conflict(error: Exception) -> bool:
    response = getattr(error, "response", {})
    code = response.get("Error", {}).get("Code") if isinstance(response, Mapping) else None
    return code == "ConditionalCheckFailedException"


class DynamoDbLedger:
    """DynamoDB Table adapter using one conditional record per task action."""

    def __init__(self, table: Any) -> None:
        self._table = table

    def _record(self, task_id: str, tool_name: str) -> dict[str, Any] | None:
        try:
            return self._table.get_item(Key=_key(task_id, tool_name), ConsistentRead=True).get("Item")
        except Exception as exc:
            raise ContractError(ErrorCode.PROVIDER_FAILURE, "ledger read failed") from exc

    def _expire(self, task_id: str, tool_name: str, now: datetime) -> ApprovalRequest | None:
        try:
            response = self._table.update_item(
                Key=_key(task_id, tool_name),
                UpdateExpression="SET #status = :expired",
                ConditionExpression="#status IN (:pending, :approved) AND expires_at <= :now",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":pending": ApprovalStatus.PENDING.value,
                    ":approved": ApprovalStatus.APPROVED.value,
                    ":expired": ApprovalStatus.EXPIRED.value,
                    ":now": _iso(now),
                },
                ReturnValues="ALL_NEW",
            )
            return _read_approval(response.get("Attributes"))
        except Exception as exc:
            if not _conflict(exc):
                raise ContractError(ErrorCode.PROVIDER_FAILURE, "approval expiry update failed") from exc
            return _read_approval(self._record(task_id, tool_name))

    def ensure_approval(self, request: ApprovalRequest, now: datetime) -> ApprovalRequest:
        try:
            self._table.put_item(
                Item=_approval_item(request),
                ConditionExpression="attribute_not_exists(record_key)",
            )
            return request
        except Exception as exc:
            if not _conflict(exc):
                raise ContractError(ErrorCode.PROVIDER_FAILURE, "approval storage failed") from exc
        existing = _read_approval(self._record(request.task_id, request.tool_name))
        if existing is None:
            raise ContractError(ErrorCode.PROVIDER_FAILURE, "approval conflict could not be resolved")
        if existing.action_digest != request.action_digest:
            raise ContractError(
                ErrorCode.APPROVAL_ARGUMENT_MISMATCH,
                "task already has an approval request for different arguments",
            )
        if existing.status is ApprovalStatus.EXPIRED or (
            existing.expires_at <= now and existing.status is not ApprovalStatus.CONSUMED
        ):
            self._expire(request.task_id, request.tool_name, now)
            raise ContractError(ErrorCode.APPROVAL_EXPIRED, "approval has expired")
        return existing

    def approve(
        self,
        task_id: str,
        tool_name: str,
        approval_request_id: str,
        now: datetime,
    ) -> ApprovalRequest:
        try:
            response = self._table.update_item(
                Key=_key(task_id, tool_name),
                UpdateExpression="SET #status = :approved",
                ConditionExpression=(
                    "approval_request_id = :approval_id AND #status IN (:pending, :approved) "
                    "AND expires_at > :now"
                ),
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":approval_id": approval_request_id,
                    ":pending": ApprovalStatus.PENDING.value,
                    ":approved": ApprovalStatus.APPROVED.value,
                    ":now": _iso(now),
                },
                ReturnValues="ALL_NEW",
            )
            approval = _read_approval(response.get("Attributes"))
            if approval is None:
                raise ContractError(ErrorCode.PROVIDER_FAILURE, "approval update returned no record")
            return approval
        except ContractError:
            raise
        except Exception as exc:
            if not _conflict(exc):
                raise ContractError(ErrorCode.PROVIDER_FAILURE, "approval update failed") from exc
        approval = _read_approval(self._record(task_id, tool_name))
        if approval is None or approval.approval_request_id != approval_request_id:
            raise ContractError(ErrorCode.APPROVAL_ARGUMENT_MISMATCH, "approval request does not match")
        if approval.expires_at <= now or approval.status is ApprovalStatus.EXPIRED:
            self._expire(task_id, tool_name, now)
            raise ContractError(ErrorCode.APPROVAL_EXPIRED, "approval has expired")
        if approval.status is ApprovalStatus.CONSUMED:
            raise ContractError(ErrorCode.APPROVAL_ALREADY_CONSUMED, "approval has already been consumed")
        raise ContractError(ErrorCode.PROVIDER_FAILURE, "approval update conflict could not be resolved")

    def commit(
        self,
        task_id: str,
        tool_name: str,
        approval_request_id: str,
        action_digest: str,
        idempotency_key: str,
        now: datetime,
    ) -> ExecutionReceipt:
        item = self._record(task_id, tool_name)
        existing = _read_receipt(item)
        if existing is not None:
            return _validate_receipt(existing, task_id, action_digest, idempotency_key)
        receipt = _execution_receipt(task_id, action_digest, idempotency_key, now)
        try:
            response = self._table.update_item(
                Key=_key(task_id, tool_name),
                UpdateExpression=(
                    "SET #status = :consumed, execution_id = :execution_id, "
                    "idempotency_key = :idempotency_key, effect_count = :effect_count, "
                    "provider_operation_id = :provider_operation_id, created_at = :created_at"
                ),
                ConditionExpression=(
                    "approval_request_id = :approval_id AND action_digest = :digest "
                    "AND #status = :approved AND expires_at > :now"
                ),
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":approval_id": approval_request_id,
                    ":digest": action_digest,
                    ":approved": ApprovalStatus.APPROVED.value,
                    ":consumed": ApprovalStatus.CONSUMED.value,
                    ":now": _iso(now),
                    ":execution_id": receipt.execution_id,
                    ":idempotency_key": receipt.idempotency_key,
                    ":effect_count": receipt.effect_count,
                    ":provider_operation_id": receipt.provider_operation_id,
                    ":created_at": _iso(receipt.created_at),
                },
                ReturnValues="ALL_NEW",
            )
            committed = _read_receipt(response.get("Attributes"))
            if committed is None:
                raise ContractError(ErrorCode.PROVIDER_FAILURE, "execution update returned no receipt")
            return committed
        except ContractError:
            raise
        except Exception as exc:
            if not _conflict(exc):
                raise ContractError(ErrorCode.PROVIDER_FAILURE, "execution update failed") from exc
        item = self._record(task_id, tool_name)
        existing = _read_receipt(item)
        if existing is not None:
            return _validate_receipt(existing, task_id, action_digest, idempotency_key)
        _validate_approval(_read_approval(item), approval_request_id, action_digest, now)
        raise ContractError(ErrorCode.PROVIDER_FAILURE, "execution conflict could not be resolved")

    def reconcile(
        self,
        task_id: str,
        tool_name: str,
        action_digest: str,
        idempotency_key: str,
        now: datetime,
    ) -> ExecutionReceipt:
        item = self._record(task_id, tool_name)
        receipt = _read_receipt(item)
        if receipt is None:
            raise ContractError(ErrorCode.RECONCILIATION_FAILED, "no execution receipt was found")
        _validate_receipt(receipt, task_id, action_digest, idempotency_key)
        if receipt.reconciled_at is not None:
            return receipt
        try:
            response = self._table.update_item(
                Key=_key(task_id, tool_name),
                UpdateExpression="SET reconciled_at = :now",
                ConditionExpression="attribute_not_exists(reconciled_at)",
                ExpressionAttributeValues={":now": _iso(now)},
                ReturnValues="ALL_NEW",
            )
            reconciled = _read_receipt(response.get("Attributes"))
            if reconciled is None:
                raise ContractError(ErrorCode.PROVIDER_FAILURE, "reconciliation returned no receipt")
            return reconciled
        except ContractError:
            raise
        except Exception as exc:
            if not _conflict(exc):
                raise ContractError(ErrorCode.PROVIDER_FAILURE, "reconciliation update failed") from exc
        reconciled = _read_receipt(self._record(task_id, tool_name))
        if reconciled is None:
            raise ContractError(ErrorCode.RECONCILIATION_FAILED, "execution receipt disappeared")
        return _validate_receipt(reconciled, task_id, action_digest, idempotency_key)
