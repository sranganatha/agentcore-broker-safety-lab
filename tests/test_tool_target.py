from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
import unittest

from agentcore_broker_safety_lab.contracts import ContractError, ErrorCode
from agentcore_broker_safety_lab.ledger import InMemoryLedger
from agentcore_broker_safety_lab.tool_target import BrokerToolTarget


def context(tool_name: str) -> SimpleNamespace:
    return SimpleNamespace(
        client_context=SimpleNamespace(
            custom={"bedrockAgentCoreToolName": f"BrokerTools___{tool_name}"}
        )
    )


class BrokerToolTargetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
        self.ledger = InMemoryLedger()
        self.target = BrokerToolTarget(self.ledger)
        self.execution = {
            "task_id": "task-001",
            "service_id": "claims-api",
            "environment": "demo",
            "action": "restart_worker_pool",
            "idempotency_key": "idem-001",
        }

    def request_and_approve(self) -> str:
        pending = self.target.handle(self.execution, context("execute_remediation"), now=self.now)
        approval_request_id = pending["approval_request_id"]
        self.target.approve("task-001", approval_request_id, now=self.now + timedelta(seconds=1))
        return approval_request_id

    def test_read_tools_return_exact_synthetic_evidence(self) -> None:
        service = self.target.handle(
            {"service_id": "claims-api", "environment": "demo"},
            context("inspect_service"),
            now=self.now,
        )
        runbook = self.target.handle(
            {"service_id": "claims-api", "alarm_code": "HIGH_ERROR_RATE"},
            context("get_runbook_procedure"),
            now=self.now,
        )

        self.assertEqual("DEGRADED", service["health"])
        self.assertEqual("HIGH_ERROR_RATE", service["active_alarm"])
        self.assertEqual("restart_worker_pool", runbook["allowed_action"])
        self.assertEqual(64, len(runbook["source_digest"]))

    def test_context_and_arguments_reject_unknown_or_extra_input(self) -> None:
        for invalid_context in (
            SimpleNamespace(client_context=None),
            context("fabricated_tool"),
        ):
            with self.subTest(context=invalid_context), self.assertRaises(ContractError):
                self.target.handle({}, invalid_context, now=self.now)

        with self.assertRaises(ContractError) as caught:
            self.target.handle(
                {"service_id": "claims-api", "environment": "demo", "extra": True},
                context("inspect_service"),
                now=self.now,
            )
        self.assertEqual(ErrorCode.INVALID_REQUEST, caught.exception.code)

        with self.assertRaises(ContractError) as oversized:
            self.target.handle(
                {"service_id": "x" * 129, "environment": "demo"},
                context("inspect_service"),
                now=self.now,
            )
        self.assertEqual(ErrorCode.INVALID_REQUEST, oversized.exception.code)

    def test_unapproved_execution_returns_one_exact_request(self) -> None:
        first = self.target.handle(self.execution, context("execute_remediation"), now=self.now)
        second = self.target.handle(self.execution, context("execute_remediation"), now=self.now)

        self.assertEqual(ErrorCode.APPROVAL_REQUIRED.value, first["status"])
        self.assertEqual(first, second)
        self.assertEqual(0, self.ledger.effect_count)

    def test_changed_expired_and_consumed_approvals_fail(self) -> None:
        approval_request_id = self.request_and_approve()

        with self.assertRaises(ContractError) as changed:
            self.target.handle(
                {**self.execution, "environment": "other"},
                context("execute_remediation"),
                now=self.now + timedelta(seconds=2),
            )
        self.assertEqual(ErrorCode.ACTION_NOT_ALLOWED, changed.exception.code)

        committed = self.target.handle(
            self.execution,
            context("execute_remediation"),
            now=self.now + timedelta(seconds=2),
        )
        self.assertEqual("COMMITTED", committed["status"])

        with self.assertRaises(ContractError) as consumed:
            self.target.approve(
                "task-001",
                approval_request_id,
                now=self.now + timedelta(seconds=3),
            )
        self.assertEqual(ErrorCode.APPROVAL_ALREADY_CONSUMED, consumed.exception.code)

        expiring = {**self.execution, "task_id": "task-expired", "idempotency_key": "idem-expired"}
        pending = self.target.handle(expiring, context("execute_remediation"), now=self.now)
        with self.assertRaises(ContractError) as expired:
            self.target.approve(
                "task-expired",
                pending["approval_request_id"],
                now=self.now + timedelta(minutes=5),
            )
        self.assertEqual(ErrorCode.APPROVAL_EXPIRED, expired.exception.code)

    def test_changed_approved_arguments_fail_before_execution(self) -> None:
        self.request_and_approve()
        changed = {**self.execution, "idempotency_key": "different-key"}

        with self.assertRaises(ContractError) as caught:
            self.target.handle(changed, context("execute_remediation"), now=self.now + timedelta(seconds=2))

        self.assertEqual(ErrorCode.APPROVAL_ARGUMENT_MISMATCH, caught.exception.code)
        self.assertEqual(0, self.ledger.effect_count)

    def test_repeated_and_concurrent_execution_commits_one_effect(self) -> None:
        self.request_and_approve()

        def execute() -> dict[str, object]:
            return self.target.handle(
                self.execution,
                context("execute_remediation"),
                now=self.now + timedelta(seconds=2),
            )

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: execute(), range(16)))

        self.assertEqual(1, self.ledger.effect_count)
        self.assertEqual(1, len({result["execution_id"] for result in results}))
        self.assertEqual({1}, {result["effect_count"] for result in results})

    def test_post_commit_response_loss_reconciles_existing_receipt(self) -> None:
        self.request_and_approve()

        with self.assertRaises(ContractError) as unknown:
            self.target.handle(
                self.execution,
                context("execute_remediation"),
                now=self.now + timedelta(seconds=2),
                lose_response_after_commit=True,
            )
        self.assertEqual(ErrorCode.OUTCOME_UNKNOWN, unknown.exception.code)
        self.assertEqual(1, self.ledger.effect_count)

        reconciled = self.target.reconcile_execution(
            self.execution,
            now=self.now + timedelta(seconds=3),
        )
        repeated = self.target.handle(
            self.execution,
            context("execute_remediation"),
            now=self.now + timedelta(seconds=4),
        )

        self.assertEqual("COMMITTED", reconciled["status"])
        self.assertEqual(reconciled, repeated)
        self.assertEqual(1, self.ledger.effect_count)

    def test_disallowed_action_never_creates_approval(self) -> None:
        with self.assertRaises(ContractError) as caught:
            self.target.handle(
                {**self.execution, "action": "delete_database"},
                context("execute_remediation"),
                now=self.now,
            )

        self.assertEqual(ErrorCode.ACTION_NOT_ALLOWED, caught.exception.code)
        self.assertEqual(0, self.ledger.effect_count)


if __name__ == "__main__":
    unittest.main()
