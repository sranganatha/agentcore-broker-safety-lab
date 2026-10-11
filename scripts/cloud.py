"""Bounded AWS deployment and direct Gateway verification for the lab."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
import time
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.exceptions import ClientError, WaiterError


ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS = ROOT / "artifacts"
PROJECT = "agentcore-broker-safety-lab"
ARTIFACT_PREFIX = f"{PROJECT}/"
MCP_VERSION = "2025-06-18"


def _settings() -> tuple[boto3.Session, str, str, str]:
    region = os.environ.get("AWS_REGION", "us-east-1")
    application_stack = os.environ.get("APPLICATION_STACK", "agentcore-broker-safety-lab")
    bootstrap_stack = os.environ.get("BOOTSTRAP_STACK", "agentcore-broker-safety-lab-bootstrap")
    for value in (application_stack, bootstrap_stack):
        if not value.startswith(PROJECT):
            raise SystemExit("stack names must begin with agentcore-broker-safety-lab")
    session = boto3.Session(region_name=region)
    return session, region, application_stack, bootstrap_stack


def _identity(session: boto3.Session, region: str, application_stack: str, bootstrap_stack: str) -> str:
    account = session.client("sts").get_caller_identity()["Account"]
    print(f"account=********{account[-4:]}")
    print(f"region={region}")
    print(f"application_stack={application_stack}")
    print(f"bootstrap_stack={bootstrap_stack}")
    print("model=not-used")
    return account


def _confirm(name: str) -> None:
    if os.environ.get(name) != PROJECT:
        raise SystemExit(f"set {name}={PROJECT} to continue")


def _template(name: str) -> str:
    return (ROOT / "infra" / name).read_text(encoding="utf-8")


def _stack(client: Any, name: str) -> dict[str, Any] | None:
    try:
        return client.describe_stacks(StackName=name)["Stacks"][0]
    except ClientError as exc:
        if "does not exist" in str(exc):
            return None
        raise


def _outputs(stack: dict[str, Any]) -> dict[str, str]:
    return {item["OutputKey"]: item["OutputValue"] for item in stack.get("Outputs", [])}


def _deploy_stack(
    client: Any,
    name: str,
    template_body: str,
    parameters: list[dict[str, str]],
    *,
    capabilities: list[str] | None = None,
) -> None:
    current = _stack(client, name)
    if current is not None and current["StackStatus"] in {"CREATE_FAILED", "ROLLBACK_COMPLETE"}:
        client.delete_stack(StackName=name)
        client.get_waiter("stack_delete_complete").wait(
            StackName=name, WaiterConfig={"Delay": 10, "MaxAttempts": 60}
        )
        current = None
    arguments: dict[str, Any] = {
        "StackName": name,
        "TemplateBody": template_body,
        "Parameters": parameters,
        "Tags": [{"Key": "Project", "Value": PROJECT}],
    }
    if capabilities:
        arguments["Capabilities"] = capabilities
    if current is None:
        client.create_stack(**arguments, OnFailure="DO_NOTHING")
        waiter = client.get_waiter("stack_create_complete")
    else:
        try:
            client.update_stack(**arguments)
        except ClientError as exc:
            if "No updates are to be performed" in str(exc):
                print(f"stack={name} unchanged")
                return
            raise
        waiter = client.get_waiter("stack_update_complete")
    try:
        waiter.wait(StackName=name, WaiterConfig={"Delay": 15, "MaxAttempts": 80})
    except WaiterError:
        events = client.describe_stack_events(StackName=name).get("StackEvents", [])
        for event in events:
            if "FAILED" in event["ResourceStatus"]:
                print(
                    f"failed_resource={event['LogicalResourceId']} "
                    f"reason={_redact_text(event.get('ResourceStatusReason', 'unknown'))}"
                )
        raise
    print(f"stack={name} ready")


def deploy() -> None:
    _confirm("CONFIRM_DEPLOY")
    session, region, application_stack, bootstrap_stack = _settings()
    account = _identity(session, region, application_stack, bootstrap_stack)
    manifest = json.loads((ARTIFACTS / "lambda-package.json").read_text(encoding="utf-8"))
    package = ARTIFACTS / manifest["file"]
    if not package.is_file():
        raise SystemExit("run make package-aws first")

    cloudformation = session.client("cloudformation")
    _deploy_stack(cloudformation, bootstrap_stack, _template("bootstrap.json"), [])
    bootstrap = _stack(cloudformation, bootstrap_stack)
    if bootstrap is None:
        raise RuntimeError("bootstrap stack disappeared")
    bucket = _outputs(bootstrap)["ArtifactBucketName"]
    key = f"{ARTIFACT_PREFIX}{manifest['sha256']}/broker-tool-target.zip"
    session.client("s3").put_object(
        Bucket=bucket,
        Key=key,
        Body=package.read_bytes(),
        ServerSideEncryption="AES256",
    )
    _deploy_stack(
        cloudformation,
        application_stack,
        _template("application.json"),
        [
            {"ParameterKey": "ArtifactBucketName", "ParameterValue": bucket},
            {"ParameterKey": "ArtifactObjectKey", "ParameterValue": key},
            {"ParameterKey": "PolicyEngineMode", "ParameterValue": "LOG_ONLY"},
        ],
        capabilities=["CAPABILITY_IAM"],
    )
    application = _stack(cloudformation, application_stack)
    if application is None:
        raise RuntimeError("application stack disappeared")
    deployed = _outputs(application)
    ARTIFACTS.mkdir(exist_ok=True)
    (ARTIFACTS / "aws-deployment.json").write_text(
        json.dumps(
            {
                "account": f"********{account[-4:]}",
                "region": region,
                "application_stack": application_stack,
                "bootstrap_stack": bootstrap_stack,
                "gateway_id": deployed["GatewayIdentifier"],
                "policy_mode": deployed["PolicyEngineMode"],
                "artifact_sha256": manifest["sha256"],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"gateway_id={deployed['GatewayIdentifier']}")
    print("policy_mode=LOG_ONLY")


def set_policy_mode(mode: str) -> None:
    if mode == "ENFORCE":
        _confirm("CONFIRM_POLICY_ENFORCE")
    session, region, application_stack, bootstrap_stack = _settings()
    _identity(session, region, application_stack, bootstrap_stack)
    cloudformation = session.client("cloudformation")
    current = _stack(cloudformation, application_stack)
    if current is None:
        raise SystemExit("application stack does not exist")
    parameters = []
    for parameter in current.get("Parameters", []):
        value = mode if parameter["ParameterKey"] == "PolicyEngineMode" else parameter["ParameterValue"]
        parameters.append({"ParameterKey": parameter["ParameterKey"], "ParameterValue": value})
    _deploy_stack(
        cloudformation,
        application_stack,
        _template("application.json"),
        parameters,
        capabilities=["CAPABILITY_IAM"],
    )
    actual = _outputs(_stack(cloudformation, application_stack) or {})["PolicyEngineMode"]
    if actual != mode:
        raise RuntimeError(f"policy mode is {actual}, expected {mode}")
    print(f"policy_mode={actual}")


class McpClient:
    def __init__(self, session: boto3.Session, region: str, url: str) -> None:
        self._session = session
        self._region = region
        self._url = url
        self._session_id: str | None = None
        self._request_id = 0

    def initialize(self) -> dict[str, Any]:
        response, headers = self._request(
            {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "initialize",
                "params": {
                    "protocolVersion": MCP_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": PROJECT, "version": "0.1.0"},
                },
            }
        )
        if _is_error(response):
            raise RuntimeError("Gateway rejected MCP initialization")
        self._session_id = headers.get("mcp-session-id")
        self._request({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return response

    def tools(self) -> dict[str, Any]:
        return self._request(
            {"jsonrpc": "2.0", "id": self._next_id(), "method": "tools/list", "params": {}}
        )[0]

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._request(
            {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )[0]

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _request(self, payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": MCP_VERSION,
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        credentials = self._session.get_credentials()
        if credentials is None:
            raise RuntimeError("AWS credentials are unavailable")
        aws_request = AWSRequest(method="POST", url=self._url, data=body, headers=headers)
        SigV4Auth(credentials.get_frozen_credentials(), "bedrock-agentcore", self._region).add_auth(aws_request)
        request = Request(self._url, data=body, headers=dict(aws_request.headers), method="POST")
        try:
            with urlopen(request, timeout=30) as response:
                raw = response.read().decode("utf-8")
                response_headers = {key.lower(): value for key, value in response.headers.items()}
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            response_headers = {key.lower(): value for key, value in exc.headers.items()}
            return {"error": {"code": exc.code, "message": _redact_text(raw)}}, response_headers
        if not raw.strip():
            return {}, response_headers
        return _parse_mcp(raw), response_headers


def _parse_mcp(raw: str) -> dict[str, Any]:
    if raw.lstrip().startswith("{"):
        return json.loads(raw)
    for line in raw.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:].strip())
    raise RuntimeError("Gateway returned an unsupported MCP response")


def _redact_text(value: str) -> str:
    return re.sub(r"(?<!\d)\d{12}(?!\d)", "************", value[:500]).replace("\n", " ")


def _is_error(response: dict[str, Any]) -> bool:
    if "error" in response:
        return True
    result = response.get("result", {})
    return bool(isinstance(result, dict) and result.get("isError"))


def _tool_names(response: dict[str, Any]) -> list[str]:
    result = response.get("result", {})
    tools = result.get("tools", []) if isinstance(result, dict) else []
    return sorted(tool.get("name", "") for tool in tools if isinstance(tool, dict))


def _dispatch_events(logs: Any, log_group: str, start_ms: int) -> list[dict[str, Any]]:
    events = logs.filter_log_events(logGroupName=log_group, startTime=start_ms).get("events", [])
    records: list[dict[str, Any]] = []
    for event in events:
        message = event.get("message", "").strip()
        if not message.startswith("{"):
            continue
        try:
            record = json.loads(message)
        except json.JSONDecodeError:
            continue
        if record.get("event_type") == "tool_dispatch":
            records.append(record)
    return records


def verify(mode: str) -> None:
    session, region, application_stack, bootstrap_stack = _settings()
    account = _identity(session, region, application_stack, bootstrap_stack)
    cloudformation = session.client("cloudformation")
    stack = _stack(cloudformation, application_stack)
    if stack is None:
        raise SystemExit("application stack does not exist")
    outputs = _outputs(stack)
    if outputs["PolicyEngineMode"] != mode:
        raise SystemExit(f"stack policy mode is {outputs['PolicyEngineMode']}, expected {mode}")

    started_ms = int(time.time() * 1000)
    client = McpClient(session, region, outputs["GatewayUrl"])
    client.initialize()
    listed = client.tools()
    expected_tools = sorted(
        [
            "BrokerTools___execute_remediation",
            "BrokerTools___get_runbook_procedure",
            "BrokerTools___inspect_service",
        ]
    )
    runbook = client.call(
        "BrokerTools___get_runbook_procedure",
        {"service_id": "claims-api", "alarm_code": "HIGH_ERROR_RATE"},
    )
    inspection = client.call(
        "BrokerTools___inspect_service",
        {"service_id": "claims-api", "environment": "demo"},
    )
    unknown = client.call("BrokerTools___delete_database", {})
    disallowed = client.call(
        "BrokerTools___execute_remediation",
        {
            "task_id": f"verify-{mode.lower()}",
            "service_id": "claims-api",
            "environment": "demo",
            "action": "delete_database",
            "idempotency_key": f"verify-{mode.lower()}-unsafe",
        },
    )
    time.sleep(10)
    dispatches = _dispatch_events(session.client("logs"), outputs["ToolLambdaLogGroupName"], started_ms)
    counts: dict[str, int] = {}
    for record in dispatches:
        tool = str(record.get("tool_name", "unknown"))
        counts[tool] = counts.get(tool, 0) + 1

    cases = {
        "discover_exact_surface": _tool_names(listed) == expected_tools,
        "allowed_runbook": not _is_error(runbook),
        "allowed_inspection": not _is_error(inspection),
        "unknown_tool_rejected": _is_error(unknown),
        "disallowed_action_rejected": _is_error(disallowed),
        "allowed_reads_reached_lambda": counts.get("get_runbook_procedure") == 1
        and counts.get("inspect_service") == 1,
        "unknown_tool_did_not_reach_lambda": counts.get("unknown", 0) == 0,
    }
    if mode == "ENFORCE":
        cases["disallowed_action_did_not_reach_lambda"] = counts.get("execute_remediation", 0) == 0
    else:
        cases["shadow_action_reached_target_validation"] = counts.get("execute_remediation", 0) == 1

    report = {
        "account": f"********{account[-4:]}",
        "region": region,
        "execution_mode": "AWS",
        "model": "not-used",
        "policy_engine_mode": mode,
        "mcp_version": MCP_VERSION,
        "gateway_id": outputs["GatewayIdentifier"],
        "expected_tools": expected_tools,
        "actual_tools": _tool_names(listed),
        "dispatch_counts": counts,
        "cases": cases,
        "passed": all(cases.values()),
        "verified_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    path = ARTIFACTS / f"aws-verification-{mode.lower()}.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"report={path.name}")
    print(f"passed={str(report['passed']).lower()}")
    if not report["passed"]:
        raise SystemExit("AWS verification failed")


def destroy() -> None:
    _confirm("CONFIRM_DESTROY")
    session, region, application_stack, bootstrap_stack = _settings()
    account = _identity(session, region, application_stack, bootstrap_stack)
    cloudformation = session.client("cloudformation")
    application = _stack(cloudformation, application_stack)
    if application is not None:
        cloudformation.delete_stack(StackName=application_stack)
        cloudformation.get_waiter("stack_delete_complete").wait(
            StackName=application_stack, WaiterConfig={"Delay": 15, "MaxAttempts": 80}
        )
        print(f"stack={application_stack} deleted")
    bootstrap = _stack(cloudformation, bootstrap_stack)
    if bootstrap is not None:
        bucket = _outputs(bootstrap)["ArtifactBucketName"]
        s3 = session.client("s3")
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=ARTIFACT_PREFIX):
            objects = [{"Key": item["Key"]} for item in page.get("Contents", [])]
            if objects:
                s3.delete_objects(Bucket=bucket, Delete={"Objects": objects, "Quiet": True})
        cloudformation.delete_stack(StackName=bootstrap_stack)
        cloudformation.get_waiter("stack_delete_complete").wait(
            StackName=bootstrap_stack, WaiterConfig={"Delay": 15, "MaxAttempts": 80}
        )
        print(f"stack={bootstrap_stack} deleted")
    report = {
        "account": f"********{account[-4:]}",
        "region": region,
        "application_stack_absent": _stack(cloudformation, application_stack) is None,
        "bootstrap_stack_absent": _stack(cloudformation, bootstrap_stack) is None,
        "verified_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    (ARTIFACTS / "aws-teardown.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("deploy")
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--mode", choices=["LOG_ONLY", "ENFORCE"], required=True)
    mode_parser = subparsers.add_parser("policy-mode")
    mode_parser.add_argument("--mode", choices=["LOG_ONLY", "ENFORCE"], required=True)
    subparsers.add_parser("destroy")
    arguments = parser.parse_args()
    if arguments.command == "deploy":
        deploy()
    elif arguments.command == "verify":
        verify(arguments.mode)
    elif arguments.command == "policy-mode":
        set_policy_mode(arguments.mode)
    else:
        destroy()


if __name__ == "__main__":
    main()
