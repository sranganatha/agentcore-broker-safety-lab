from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import unittest

from agentcore_broker_safety_lab.canonical import action_digest, approval_request_id
from agentcore_broker_safety_lab.contracts import ApprovalRequest, ApprovalStatus, ContractError, ErrorCode
from agentcore_broker_safety_lab.ledger import DynamoDbLedger


class ConditionalFailure(Exception):
    def __init__(self) -> None:
        super().__init__("conditional write failed")
        self.response = {"Error": {"Code": "ConditionalCheckFailedException"}}


class FakeDynamoTable:
    def __init__(self) -> None:
        self.items: dict[str, dict] = {}
        self.last_update: dict | None = None

    def get_item(self, **request: object) -> dict:
        item = self.items.get(request["Key"]["record_key"])  # type: ignore[index]
        return {"Item": deepcopy(item)} if item else {}

    def put_item(self, **request: object) -> dict:
        item = request["Item"]  # type: ignore[assignment]
        key = item["record_key"]
        if key in self.items:
            raise ConditionalFailure()
        self.items[key] = deepcopy(item)
        return {}

    def update_item(self, **request: object) -> dict:
        self.last_update = deepcopy(request)
        key = request["Key"]["record_key"]  # type: ignore[index]
        item = self.items.get(key)
        if item is None:
            raise ConditionalFailure()
        values = request["ExpressionAttributeValues"]  # type: ignore[assignment]
        if ":execution_id" in values:
            if (
                item["approval_request_id"] != values[":approval_id"]
                or item["action_digest"] != values[":digest"]
                or item["status"] != "APPROVED"
                or item["expires_at"] <= values[":now"]
            ):
                raise ConditionalFailure()
            item.update(
                status="CONSUMED",
                execution_id=values[":execution_id"],
                idempotency_key=values[":idempotency_key"],
                effect_count=Decimal(values[":effect_count"]),
                provider_operation_id=values[":provider_operation_id"],
                created_at=values[":created_at"],
            )
        elif ":expired" in values:
            if item["status"] not in {"PENDING", "APPROVED"} or item["expires_at"] > values[":now"]:
                raise ConditionalFailure()
            item["status"] = "EXPIRED"
        elif ":approved" in values:
            if (
                item["approval_request_id"] != values[":approval_id"]
                or item["status"] not in {"PENDING", "APPROVED"}
                or item["expires_at"] <= values[":now"]
            ):
                raise ConditionalFailure()
            item["status"] = "APPROVED"
        else:
            if "reconciled_at" in item:
                raise ConditionalFailure()
            item["reconciled_at"] = values[":now"]
        return {"Attributes": deepcopy(item)}


class DynamoDbLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.table = FakeDynamoTable()
        self.ledger = DynamoDbLedger(self.table)
        self.now = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
        arguments = {
            "task_id": "task-001",
            "service_id": "claims-api",
            "environment": "demo",
            "action": "restart_worker_pool",
            "idempotency_key": "idem-001",
        }
        self.digest = action_digest("task-001", "execute_remediation", arguments)
        self.request = ApprovalRequest(
            approval_request_id=approval_request_id(self.digest),
            task_id="task-001",
            tool_name="execute_remediation",
            action_digest=self.digest,
            action_summary="restart_worker_pool for claims-api in demo",
            expires_at=self.now + timedelta(minutes=5),
            status=ApprovalStatus.PENDING,
        )

    def test_conditional_record_covers_approval_commit_and_reconciliation(self) -> None:
        self.assertEqual(self.request, self.ledger.ensure_approval(self.request, self.now))
        self.assertEqual(self.request, self.ledger.ensure_approval(self.request, self.now))
        approved = self.ledger.approve(
            "task-001", "execute_remediation", self.request.approval_request_id, self.now
        )
        self.assertEqual(ApprovalStatus.APPROVED, approved.status)

        receipt = self.ledger.commit(
            "task-001",
            "execute_remediation",
            self.request.approval_request_id,
            self.digest,
            "idem-001",
            self.now,
        )
        repeated = self.ledger.commit(
            "task-001",
            "execute_remediation",
            self.request.approval_request_id,
            self.digest,
            "idem-001",
            self.now,
        )
        reconciled = self.ledger.reconcile(
            "task-001", "execute_remediation", self.digest, "idem-001", self.now
        )

        self.assertEqual(receipt, repeated)
        self.assertEqual(1, receipt.effect_count)
        self.assertEqual(self.now, reconciled.reconciled_at)
        record = self.table.items["APPROVAL#task-001#execute_remediation"]
        self.assertEqual("CONSUMED", record["status"])
        self.assertEqual(1, record["effect_count"])

        with self.assertRaises(ContractError) as consumed:
            self.ledger.approve(
                "task-001", "execute_remediation", self.request.approval_request_id, self.now
            )
        self.assertEqual(ErrorCode.APPROVAL_ALREADY_CONSUMED, consumed.exception.code)

    def test_changed_and_expired_approvals_fail_closed(self) -> None:
        self.ledger.ensure_approval(self.request, self.now)
        changed_digest = action_digest(
            "task-001",
            "execute_remediation",
            {"idempotency_key": "changed"},
        )
        changed = ApprovalRequest(
            approval_request_id=approval_request_id(changed_digest),
            task_id="task-001",
            tool_name="execute_remediation",
            action_digest=changed_digest,
            action_summary=self.request.action_summary,
            expires_at=self.request.expires_at,
            status=ApprovalStatus.PENDING,
        )
        with self.assertRaises(ContractError) as mismatch:
            self.ledger.ensure_approval(changed, self.now)
        self.assertEqual(ErrorCode.APPROVAL_ARGUMENT_MISMATCH, mismatch.exception.code)

        with self.assertRaises(ContractError) as expired:
            self.ledger.approve(
                "task-001",
                "execute_remediation",
                self.request.approval_request_id,
                self.request.expires_at,
            )
        self.assertEqual(ErrorCode.APPROVAL_EXPIRED, expired.exception.code)
        self.assertEqual("EXPIRED", self.table.items["APPROVAL#task-001#execute_remediation"]["status"])

    def test_provider_read_failure_keeps_cause_and_returns_stable_code(self) -> None:
        class BrokenTable:
            def get_item(self, **request: object) -> dict:
                raise RuntimeError("unredacted provider detail")

        with self.assertRaises(ContractError) as caught:
            DynamoDbLedger(BrokenTable()).reconcile(
                "task-001", "execute_remediation", self.digest, "idem-001", self.now
            )

        self.assertEqual(ErrorCode.PROVIDER_FAILURE, caught.exception.code)
        self.assertIsInstance(caught.exception.__cause__, RuntimeError)


if __name__ == "__main__":
    unittest.main()
