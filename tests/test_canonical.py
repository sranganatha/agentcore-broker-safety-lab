import math
import unittest

from agentcore_broker_safety_lab.canonical import (
    action_digest,
    approval_request_id,
    canonical_arguments,
)
from agentcore_broker_safety_lab.contracts import ContractError, ErrorCode


class CanonicalActionTests(unittest.TestCase):
    def test_key_order_does_not_change_action_identity(self) -> None:
        first = {"service_id": "claims-api", "action": "restart_worker_pool", "options": {"b": 2, "a": 1}}
        second = {"options": {"a": 1, "b": 2}, "action": "restart_worker_pool", "service_id": "claims-api"}

        self.assertEqual(canonical_arguments(first), canonical_arguments(second))
        self.assertEqual(
            action_digest("task-001", "execute_remediation", first),
            action_digest("task-001", "execute_remediation", second),
        )

    def test_bound_values_change_action_identity(self) -> None:
        arguments = {"service_id": "claims-api", "action": "restart_worker_pool"}
        baseline = action_digest("task-001", "execute_remediation", arguments)

        self.assertNotEqual(baseline, action_digest("task-002", "execute_remediation", arguments))
        self.assertNotEqual(baseline, action_digest("task-001", "inspect_service", arguments))
        self.assertNotEqual(
            baseline,
            action_digest("task-001", "execute_remediation", {**arguments, "action": "delete_database"}),
        )

    def test_non_json_values_fail_with_stable_code(self) -> None:
        invalid_values = [
            {"bad": object()},
            {"bad": math.nan},
            {"bad": math.inf},
            {1: "non-string key"},
        ]

        for value in invalid_values:
            with self.subTest(value=value), self.assertRaises(ContractError) as caught:
                canonical_arguments(value)
            self.assertEqual(ErrorCode.INVALID_REQUEST, caught.exception.code)

    def test_approval_request_id_is_stable_and_bounded(self) -> None:
        digest = action_digest("task-001", "execute_remediation", {"action": "restart_worker_pool"})

        self.assertEqual(f"apr_{digest[:20]}", approval_request_id(digest))

        with self.assertRaises(ContractError):
            approval_request_id(digest.upper())


if __name__ == "__main__":
    unittest.main()
