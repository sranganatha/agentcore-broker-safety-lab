import json
from pathlib import Path
import unittest

from agentcore_broker_safety_lab.validate_fixtures import ROOT, validate_all


class FixtureTests(unittest.TestCase):
    def test_all_fixtures_validate(self) -> None:
        validate_all()

    def test_fixture_text_contains_no_real_endpoints_or_account_ids(self) -> None:
        fixture_text = "\n".join(
            path.read_text(encoding="utf-8") for path in sorted((ROOT / "config").glob("*.json"))
        )

        self.assertNotIn("https://", fixture_text)
        self.assertNotIn("arn:aws", fixture_text)
        self.assertNotRegex(fixture_text, r"\b\d{12}\b")

    def test_tools_are_exactly_the_public_contract(self) -> None:
        tools = json.loads((Path(ROOT) / "config" / "tools.json").read_text(encoding="utf-8"))

        self.assertEqual(
            {"get_runbook_procedure", "inspect_service", "execute_remediation"},
            {tool["name"] for tool in tools},
        )


if __name__ == "__main__":
    unittest.main()
