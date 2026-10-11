import json
import re
import unittest

from agentcore_broker_safety_lab.gateway_contract import (
    TARGET_NAME,
    evaluate_gateway_case,
    gateway_action,
)
from agentcore_broker_safety_lab.validate_fixtures import ROOT


class GatewayContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tools = json.loads((ROOT / "config" / "tools.json").read_text(encoding="utf-8"))
        cls.cases = json.loads((ROOT / "config" / "gateway_policy_cases.json").read_text(encoding="utf-8"))

    def test_gateway_visible_names_are_exact(self) -> None:
        self.assertEqual(
            {
                "BrokerTools___get_runbook_procedure",
                "BrokerTools___inspect_service",
                "BrokerTools___execute_remediation",
            },
            {gateway_action(tool["name"]) for tool in self.tools},
        )
        self.assertEqual("BrokerTools", TARGET_NAME)

    def test_offline_policy_cases_match_expected_decisions(self) -> None:
        self.assertGreaterEqual(len(self.cases), 10)
        for case in self.cases:
            with self.subTest(case=case["case_id"]):
                actual = evaluate_gateway_case(case, self.tools)
                self.assertEqual(case["expected_decision"], actual.decision)
                self.assertEqual(case["expected_layer"], actual.layer)
                self.assertEqual(case["expected_reason"], actual.reason)

    def test_case_ids_and_required_denials_are_present(self) -> None:
        case_ids = [case["case_id"] for case in self.cases]
        self.assertEqual(len(case_ids), len(set(case_ids)))
        self.assertIn("deny_missing_invoke_permission", case_ids)
        self.assertIn("deny_unknown_action", case_ids)
        self.assertIn("deny_disallowed_remediation", case_ids)
        self.assertIn("allow_database_escalation_runbook", case_ids)

    def test_cedar_policy_names_each_manifest_action_once(self) -> None:
        statement = (ROOT / "policies" / "broker.cedar").read_text(encoding="utf-8")
        policy_actions = re.findall(r'AgentCore::Action::"([^"]+)"', statement)
        expected = [gateway_action(tool["name"]) for tool in self.tools]

        self.assertCountEqual(expected, policy_actions)
        self.assertEqual(len(expected), len(policy_actions))
        self.assertIn('principal is AgentCore::IamEntity', statement)
        self.assertEqual(3, statement.count('resource == AgentCore::Gateway::"${GatewayArn}"'))


if __name__ == "__main__":
    unittest.main()
