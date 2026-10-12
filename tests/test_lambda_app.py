import json
import os
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agentcore_broker_safety_lab import lambda_app


def context(tool_name: str) -> SimpleNamespace:
    return SimpleNamespace(
        client_context=SimpleNamespace(custom={"bedrockAgentCoreToolName": f"BrokerTools___{tool_name}"})
    )


class LambdaAppTests(unittest.TestCase):
    def tearDown(self) -> None:
        lambda_app._TARGET = None

    def test_handler_uses_dynamodb_table_from_environment(self) -> None:
        fake_table = object()
        resource = SimpleNamespace(Table=lambda name: fake_table)
        target = SimpleNamespace(handle=lambda event, request_context: {"service_id": event["service_id"]})

        with (
            patch.dict(os.environ, {"LEDGER_TABLE_NAME": "lab-ledger"}, clear=True),
            patch.dict(sys.modules, {"boto3": SimpleNamespace(resource=lambda service: resource)}),
            patch.object(lambda_app, "BrokerToolTarget", return_value=target) as target_type,
            patch("builtins.print"),
        ):
            result = lambda_app.handler(
                {"service_id": "claims-api", "environment": "demo"},
                context("inspect_service"),
            )

        self.assertEqual({"service_id": "claims-api"}, result)
        target_type.assert_called_once()
        self.assertIs(fake_table, target_type.call_args.args[0]._table)

    def test_log_contains_metadata_but_not_arguments(self) -> None:
        with patch("builtins.print") as output:
            lambda_app._log("tool_dispatch", "inspect_service", "SUCCESS")

        record = json.loads(output.call_args.args[0])
        self.assertEqual(
            {"event_type": "tool_dispatch", "tool_name": "inspect_service", "outcome": "SUCCESS"},
            record,
        )


if __name__ == "__main__":
    unittest.main()
