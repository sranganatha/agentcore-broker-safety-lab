import unittest
import sys
from types import ModuleType
from typing import Any

if "boto3" not in sys.modules:
    boto3 = ModuleType("boto3")
    boto3.Session = object
    auth = ModuleType("botocore.auth")
    auth.SigV4Auth = object
    awsrequest = ModuleType("botocore.awsrequest")
    awsrequest.AWSRequest = object
    exceptions = ModuleType("botocore.exceptions")
    exceptions.ClientError = type("ClientError", (Exception,), {})
    exceptions.WaiterError = type("WaiterError", (Exception,), {})
    sys.modules.update(
        {
            "boto3": boto3,
            "botocore": ModuleType("botocore"),
            "botocore.auth": auth,
            "botocore.awsrequest": awsrequest,
            "botocore.exceptions": exceptions,
        }
    )

from scripts.cloud import McpClient, _redact_text


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


if __name__ == "__main__":
    unittest.main()
