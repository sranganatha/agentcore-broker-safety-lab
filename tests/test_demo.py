import unittest

from agentcore_broker_safety_lab.demo import run_demo


class DemoTests(unittest.TestCase):
    def test_demo_proves_policy_approval_and_reconciliation(self) -> None:
        report = run_demo()

        self.assertTrue(report["passed"])
        self.assertEqual("FIXTURE", report["execution_mode"])
        self.assertEqual("deterministic-fixture", report["model"])
        self.assertEqual(0, report["cloud_calls"])
        self.assertGreaterEqual(report["policy_case_count"], 8)
        self.assertTrue(all(report["cases"].values()))


if __name__ == "__main__":
    unittest.main()
