from datetime import UTC, datetime, timedelta
import unittest

from agentcore_broker_safety_lab.canonical import action_digest
from agentcore_broker_safety_lab.contracts import (
    ApprovalRequest,
    ApprovalStatus,
    BrokerTask,
    ContractError,
    ErrorCode,
    ExecutionReceipt,
    ExecutionStatus,
)


class ContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.digest = action_digest("task-001", "execute_remediation", {"action": "restart_worker_pool"})
        self.timestamp = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)

    def test_broker_task_rejects_missing_extra_and_blank_fields(self) -> None:
        valid = {
            "task_id": "task-001",
            "service_id": "claims-api",
            "environment": "demo",
            "alarm_code": "HIGH_ERROR_RATE",
            "requested_outcome": "Restore healthy processing",
            "correlation_id": "corr-001",
        }

        invalid_contracts = (
            {key: value for key, value in valid.items() if key != "task_id"},
            {**valid, "extra": True},
            {**valid, "task_id": " "},
        )
        for invalid in invalid_contracts:
            with self.subTest(invalid=invalid), self.assertRaises(ContractError) as caught:
                BrokerTask.from_mapping(invalid)
            self.assertEqual(ErrorCode.INVALID_REQUEST, caught.exception.code)

        with self.assertRaises(ContractError):
            BrokerTask(" ", "claims-api", "demo", "HIGH_ERROR_RATE", "Restore", "corr-001")

    def test_approval_requires_timezone_and_sha256_digest(self) -> None:
        valid = {
            "approval_request_id": "apr-001",
            "task_id": "task-001",
            "tool_name": "execute_remediation",
            "action_digest": self.digest,
            "action_summary": "Restart the synthetic worker pool",
            "expires_at": "2026-10-11T12:05:00Z",
            "status": "PENDING",
        }

        approval = ApprovalRequest.from_mapping(valid)
        self.assertEqual(ApprovalStatus.PENDING, approval.status)

        for invalid in ({**valid, "expires_at": "2026-10-11T12:05:00"}, {**valid, "action_digest": "not-a-digest"}):
            with self.subTest(invalid=invalid), self.assertRaises(ContractError):
                ApprovalRequest.from_mapping(invalid)

    def test_committed_receipt_requires_one_effect_and_provider_operation(self) -> None:
        receipt = ExecutionReceipt(
            execution_id="exec-001",
            idempotency_key="idem-001",
            task_id="task-001",
            action_digest=self.digest,
            status=ExecutionStatus.COMMITTED,
            effect_count=1,
            provider_operation_id="operation-001",
            created_at=self.timestamp,
            reconciled_at=None,
        )

        self.assertEqual(1, receipt.effect_count)

        with self.assertRaises(ContractError):
            ExecutionReceipt(
                execution_id="exec-001",
                idempotency_key="idem-001",
                task_id="task-001",
                action_digest=self.digest,
                status=ExecutionStatus.COMMITTED,
                effect_count=0,
                provider_operation_id=None,
                created_at=self.timestamp,
                reconciled_at=None,
            )

        with self.assertRaises(ContractError):
            ExecutionReceipt(
                execution_id="exec-006",
                idempotency_key="idem-006",
                task_id="task-001",
                action_digest=self.digest,
                status=ExecutionStatus.OUTCOME_UNKNOWN,
                effect_count=None,
                provider_operation_id=None,
                created_at=self.timestamp,
                reconciled_at=self.timestamp - timedelta(seconds=1),
            )

    def test_other_receipt_states_do_not_claim_unknown_effects(self) -> None:
        rejected = ExecutionReceipt(
            execution_id="exec-002",
            idempotency_key="idem-002",
            task_id="task-001",
            action_digest=self.digest,
            status=ExecutionStatus.REJECTED,
            effect_count=0,
            provider_operation_id=None,
            created_at=self.timestamp,
            reconciled_at=None,
        )
        unknown = ExecutionReceipt(
            execution_id="exec-003",
            idempotency_key="idem-003",
            task_id="task-001",
            action_digest=self.digest,
            status=ExecutionStatus.OUTCOME_UNKNOWN,
            effect_count=None,
            provider_operation_id=None,
            created_at=self.timestamp,
            reconciled_at=None,
        )

        self.assertEqual(ExecutionStatus.REJECTED, rejected.status)
        self.assertIsNone(unknown.effect_count)

        with self.assertRaises(ContractError):
            ExecutionReceipt(
                execution_id="exec-004",
                idempotency_key="idem-004",
                task_id="task-001",
                action_digest=self.digest,
                status="NOT_A_STATE",  # type: ignore[arg-type]
                effect_count=None,
                provider_operation_id=None,
                created_at=self.timestamp,
                reconciled_at=None,
            )

        with self.assertRaises(ContractError):
            ExecutionReceipt(
                execution_id="exec-005",
                idempotency_key="idem-005",
                task_id="task-001",
                action_digest=self.digest,
                status=ExecutionStatus.COMMITTED,
                effect_count=True,
                provider_operation_id="operation-005",
                created_at=self.timestamp,
                reconciled_at=None,
            )


if __name__ == "__main__":
    unittest.main()
