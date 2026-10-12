import unittest
import sys
from types import ModuleType
from typing import Any

if "boto3" not in sys.modules:
    boto3 = ModuleType("boto3")
    boto3.Session = object
    transfer = ModuleType("boto3.s3.transfer")
    transfer.TransferConfig = object
    auth = ModuleType("botocore.auth")
    auth.SigV4Auth = object
    awsrequest = ModuleType("botocore.awsrequest")
    awsrequest.AWSRequest = object
    config = ModuleType("botocore.config")
    config.Config = object
    exceptions = ModuleType("botocore.exceptions")
    exceptions.ClientError = type("ClientError", (Exception,), {})
    exceptions.WaiterError = type("WaiterError", (Exception,), {})
    sys.modules.update(
        {
            "boto3": boto3,
            "boto3.s3": ModuleType("boto3.s3"),
            "boto3.s3.transfer": transfer,
            "botocore": ModuleType("botocore"),
            "botocore.auth": auth,
            "botocore.awsrequest": awsrequest,
            "botocore.config": config,
            "botocore.exceptions": exceptions,
        }
    )

from scripts.cloud import (
    McpClient,
    _approval_markdown,
    _assume_role_session,
    _redact_text,
    _tool_content,
)


class StubMcpClient(McpClient):
    def __init__(self, responses: list[tuple[dict[str, Any], dict[str, str]]]) -> None:
        self._responses = iter(responses)
        self._session_id = None
        self._request_id = 0

    def _request(self, payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
        return next(self._responses)


class McpClientTests(unittest.TestCase):
    def test_failure_output_redacts_account_ids(self) -> None:
        self.assertEqual(
            "arn:aws:iam::************:role/example",
            _redact_text("arn:aws:iam::123456789012:role/example"),
        )

    def test_initialize_accepts_stateless_gateway(self) -> None:
        client = StubMcpClient(
            [
                ({"result": {"protocolVersion": "2025-06-18"}}, {}),
                ({}, {}),
            ]
        )

        response = client.initialize()

        self.assertEqual("2025-06-18", response["result"]["protocolVersion"])
        self.assertIsNone(client._session_id)

    def test_initialize_rejects_gateway_error(self) -> None:
        client = StubMcpClient([({"error": {"code": 400}}, {})])

        with self.assertRaisesRegex(RuntimeError, "rejected MCP initialization"):
            client.initialize()

    def test_tool_content_accepts_structured_and_text_results(self) -> None:
        self.assertEqual(
            {"status": "COMMITTED"},
            _tool_content({"result": {"structuredContent": {"status": "COMMITTED"}}}),
        )
        self.assertEqual(
            {"status": "APPROVAL_REQUIRED"},
            _tool_content(
                {
                    "result": {
                        "content": [{"text": '{"status":"APPROVAL_REQUIRED"}'}]
                    }
                }
            ),
        )

    def test_approval_markdown_contains_only_normalized_results(self) -> None:
        report = {
            "execution_mode": "AWS_APPROVAL_RECONCILIATION",
            "region": "us-east-1",
            "account": "********9012",
            "policy_engine_mode": "ENFORCE",
            "passed": True,
            "cases": {"approved_action_commits_once": True},
        }

        markdown = _approval_markdown(report)

        self.assertIn("| approved_action_commits_once | PASS |", markdown)
        self.assertNotIn("task_id", markdown)

    def test_operator_role_rejects_root_credentials(self) -> None:
        sts = type("Sts", (), {"get_caller_identity": lambda self: {"Arn": "arn:aws:iam::123456789012:root"}})()
        session = type("Session", (), {"client": lambda self, service: sts})()

        with self.assertRaisesRegex(SystemExit, "root credentials are not supported"):
            _assume_role_session(session, "arn:aws:iam::123456789012:role/operator")


if __name__ == "__main__":
    unittest.main()
