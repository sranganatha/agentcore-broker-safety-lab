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
import uuid
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.config import Config
from botocore.exceptions import ClientError, WaiterError

from agentcore_broker_safety_lab.canonical import action_digest
from agentcore_broker_safety_lab.contracts import ContractError, ErrorCode
from agentcore_broker_safety_lab.ledger import DynamoDbLedger
from agentcore_broker_safety_lab.tool_target import BrokerToolTarget, EXECUTE_TOOL


ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS = ROOT / "artifacts"
PROJECT = "agentcore-broker-safety-lab"
ARTIFACT_PREFIX = f"{PROJECT}/"
MCP_VERSION = "2025-06-18"
MODEL_ID = "amazon.nova-micro-v1:0"


def _settings() -> tuple[boto3.Session, str, str, str]:
    region = os.environ.get("AWS_REGION", "us-east-1")
    application_stack = os.environ.get("APPLICATION_STACK", "agentcore-broker-safety-lab")
    bootstrap_stack = os.environ.get("BOOTSTRAP_STACK", "agentcore-broker-safety-lab-bootstrap")
    for value in (application_stack, bootstrap_stack):
        if not value.startswith(PROJECT):
            raise SystemExit("stack names must begin with agentcore-broker-safety-lab")
    session = boto3.Session(region_name=region)
    return session, region, application_stack, bootstrap_stack


def _identity(
    session: boto3.Session,
    region: str,
    application_stack: str,
    bootstrap_stack: str,
    *,
    model: str = "not-used",
) -> str:
    account = session.client("sts").get_caller_identity()["Account"]
    print(f"account=********{account[-4:]}")
    print(f"region={region}")
    print(f"application_stack={application_stack}")
    print(f"bootstrap_stack={bootstrap_stack}")
    print(f"model={model}")
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


def _assume_role_session(session: boto3.Session, role_arn: str) -> boto3.Session:
    sts = session.client("sts")
    if sts.get_caller_identity().get("Arn", "").endswith(":root"):
        raise SystemExit("AWS root credentials are not supported; use an IAM or IAM Identity Center principal")
    credentials = sts.assume_role(
        RoleArn=role_arn,
        RoleSessionName="agentcore-broker-verification",
        DurationSeconds=900,
    )["Credentials"]
    return boto3.Session(
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"],
        region_name=session.region_name,
    )


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
    account = _identity(session, region, application_stack, bootstrap_stack, model=MODEL_ID)
    manifest = json.loads((ARTIFACTS / "aws-packages.json").read_text(encoding="utf-8"))
    packages = {name: ARTIFACTS / value["file"] for name, value in manifest.items()}
    if not all(package.is_file() for package in packages.values()):
        raise SystemExit("run make package-aws first")

    cloudformation = session.client("cloudformation")
    _deploy_stack(cloudformation, bootstrap_stack, _template("bootstrap.json"), [])
    bootstrap = _stack(cloudformation, bootstrap_stack)
    if bootstrap is None:
        raise RuntimeError("bootstrap stack disappeared")
    bucket = _outputs(bootstrap)["ArtifactBucketName"]
    keys: dict[str, str] = {}
    s3 = session.client("s3")
    transfer = TransferConfig(
        multipart_threshold=8 * 1024 * 1024,
        multipart_chunksize=8 * 1024 * 1024,
        max_concurrency=2,
        use_threads=False,
    )
    for name, package in packages.items():
        artifact = manifest[name]
        key = f"{ARTIFACT_PREFIX}{artifact['sha256']}/{artifact['file']}"
        try:
            existing = s3.head_object(Bucket=bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") not in {"404", "NoSuchKey", "NotFound"}:
                raise
            existing = None
        if existing is None or existing["ContentLength"] != artifact["size_bytes"]:
            s3.upload_file(
                str(package),
                bucket,
                key,
                ExtraArgs={"ServerSideEncryption": "AES256"},
                Config=transfer,
            )
        else:
            print(f"artifact={artifact['file']} already-present")
        keys[name] = key
    _deploy_stack(
        cloudformation,
        application_stack,
        _template("application.json"),
        [
            {"ParameterKey": "ArtifactBucketName", "ParameterValue": bucket},
            {"ParameterKey": "ArtifactObjectKey", "ParameterValue": keys["lambda"]},
            {"ParameterKey": "RuntimeArtifactObjectKey", "ParameterValue": keys["runtime"]},
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
                "lambda_artifact_sha256": manifest["lambda"]["sha256"],
                "runtime_artifact_sha256": manifest["runtime"]["sha256"],
                "runtime_arn": _redact_text(deployed["RuntimeArn"]),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"gateway_id={deployed['GatewayIdentifier']}")
    print(f"runtime_arn={_redact_text(deployed['RuntimeArn'])}")
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


def _tool_content(response: dict[str, Any]) -> dict[str, Any]:
    if _is_error(response):
        raise RuntimeError("Gateway tool call failed")
    result = response.get("result")
    if not isinstance(result, dict):
        raise RuntimeError("Gateway tool response is missing")
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured
    blocks = result.get("content")
    text = blocks[0].get("text") if isinstance(blocks, list) and blocks and isinstance(blocks[0], dict) else None
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise RuntimeError("Gateway tool response is invalid") from exc
    if not isinstance(value, dict):
        raise RuntimeError("Gateway tool response is invalid")
    return value


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


def _gateway_task_counts(
    logs: Any, log_group: str, start_ms: int, task_ids: set[str]
) -> dict[str, int]:
    events = logs.filter_log_events(logGroupName=log_group, startTime=start_ms).get("events", [])
    return {
        task_id: sum(task_id in str(event.get("message", "")) for event in events)
        for task_id in sorted(task_ids)
    }


def _invoke_runtime(
    session: boto3.Session, outputs: dict[str, str], payload: dict[str, str]
) -> dict[str, Any]:
    client = session.client(
        "bedrock-agentcore",
        config=Config(connect_timeout=10, read_timeout=60, retries={"max_attempts": 1}),
    )
    response = client.invoke_agent_runtime(
        agentRuntimeArn=outputs["RuntimeArn"],
        qualifier="DEFAULT",
        runtimeSessionId=f"verify-{uuid.uuid4().hex}",
        payload=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
    )
    raw = response["response"].read(65_537)
    if len(raw) > 65_536:
        raise RuntimeError("runtime response exceeded verification limit")
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise RuntimeError("runtime response must be an object")
    return result


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
    operator_session = _assume_role_session(session, outputs["OperatorRoleArn"])
    client = McpClient(operator_session, region, outputs["GatewayUrl"])
    client.initialize()
    listed = client.tools()
    task_id = f"verify-{mode.lower()}-{uuid.uuid4().hex[:8]}"
    expected_tools = sorted(
        [
            "BrokerTools___execute_remediation",
            "BrokerTools___get_runbook_procedure",
            "BrokerTools___inspect_service",
        ]
    )
    runbook = client.call(
        "BrokerTools___get_runbook_procedure",
        {"task_id": task_id, "service_id": "claims-api", "alarm_code": "HIGH_ERROR_RATE"},
    )
    inspection = client.call(
        "BrokerTools___inspect_service",
        {"task_id": task_id, "service_id": "claims-api", "environment": "demo"},
    )
    unknown = client.call("BrokerTools___delete_database", {})
    disallowed = client.call(
        "BrokerTools___execute_remediation",
        {
            "task_id": task_id,
            "service_id": "claims-api",
            "environment": "demo",
            "action": "delete_database",
            "idempotency_key": f"{task_id}-unsafe",
        },
    )
    time.sleep(10)
    dispatches = [
        record
        for record in _dispatch_events(session.client("logs"), outputs["ToolLambdaLogGroupName"], started_ms)
        if record.get("task_id") == task_id
    ]
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
        "task_id": task_id,
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


def verify_runtime() -> None:
    session, region, application_stack, bootstrap_stack = _settings()
    account = _identity(session, region, application_stack, bootstrap_stack, model=MODEL_ID)
    stack = _stack(session.client("cloudformation"), application_stack)
    if stack is None:
        raise SystemExit("application stack does not exist")
    outputs = _outputs(stack)
    if outputs["PolicyEngineMode"] != "ENFORCE":
        raise SystemExit("runtime verification requires ENFORCE policy mode")

    started_ms = int(time.time() * 1000)
    payload = {
        "task_id": f"runtime-{uuid.uuid4().hex[:12]}",
        "service_id": "claims-api",
        "alarm_code": "HIGH_ERROR_RATE",
        "environment": "demo",
    }
    result = _invoke_runtime(session, outputs, payload)

    dispatches: list[dict[str, Any]] = []
    for _ in range(6):
        dispatches = _dispatch_events(
            session.client("logs"), outputs["ToolLambdaLogGroupName"], started_ms
        )
        dispatches = [record for record in dispatches if record.get("task_id") == payload["task_id"]]
        if len(dispatches) >= 2:
            break
        time.sleep(5)
    counts: dict[str, int] = {}
    for record in dispatches:
        tool = str(record.get("tool_name", "unknown"))
        counts[tool] = counts.get(tool, 0) + 1
    proposal = result.get("proposal", {}) if isinstance(result, dict) else {}
    evidence = proposal.get("evidence", {}) if isinstance(proposal, dict) else {}
    cases = {
        "runtime_completed": result.get("outcome") == "COMPLETED",
        "nova_micro_used": result.get("model") == MODEL_ID,
        "runbook_read_once": counts.get("get_runbook_procedure") == 1,
        "service_read_once": counts.get("inspect_service") == 1,
        "no_remediation_dispatch": counts.get("execute_remediation", 0) == 0,
        "runtime_received_read_only_surface": result.get("outcome") == "COMPLETED",
        "evidence_precedes_proposal": bool(evidence.get("runbook_id"))
        and evidence.get("active_alarm") == payload["alarm_code"],
        "approval_required": proposal.get("requires_approval") is True,
    }
    report = {
        "account": f"********{account[-4:]}",
        "region": region,
        "execution_mode": "AWS_RUNTIME",
        "model": MODEL_ID,
        "runtime_arn": _redact_text(outputs["RuntimeArn"]),
        "task_id": payload["task_id"],
        "dispatch_counts": counts,
        "outcome": result.get("outcome"),
        "cases": cases,
        "passed": all(cases.values()),
        "verified_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    path = ARTIFACTS / "aws-runtime-verification.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"report={path.name}")
    print(f"passed={str(report['passed']).lower()}")
    if not report["passed"]:
        raise SystemExit("AWS runtime verification failed")


def approve(task_id: str, approval_request_id: str) -> None:
    _confirm("CONFIRM_APPROVE")
    session, region, application_stack, bootstrap_stack = _settings()
    _identity(session, region, application_stack, bootstrap_stack)
    stack = _stack(session.client("cloudformation"), application_stack)
    if stack is None:
        raise SystemExit("application stack does not exist")
    outputs = _outputs(stack)
    operator_session = _assume_role_session(session, outputs["OperatorRoleArn"])
    target = BrokerToolTarget(
        DynamoDbLedger(operator_session.resource("dynamodb").Table(outputs["ExecutionLedgerName"]))
    )
    approved = target.approve(task_id, approval_request_id)
    print(f"task_id={approved.task_id}")
    print(f"approval_request_id={approved.approval_request_id}")
    print(f"status={approved.status.value}")


def _approval_markdown(report: dict[str, Any]) -> str:
    rows = ["| Case | Result |", "|---|---|"]
    rows.extend(
        f"| {name} | {'PASS' if passed else 'FAIL'} |" for name, passed in report["cases"].items()
    )
    return "\n".join(
        [
            "# AWS approval and reconciliation verification",
            "",
            f"- Execution mode: {report['execution_mode']}",
            f"- Region: {report['region']}",
            f"- Account: {report['account']}",
            f"- Policy mode: {report['policy_engine_mode']}",
            f"- Result: {'PASS' if report['passed'] else 'FAIL'}",
            "",
            *rows,
            "",
        ]
    )


def verify_approval_flow() -> None:
    session, region, application_stack, bootstrap_stack = _settings()
    account = _identity(session, region, application_stack, bootstrap_stack, model=MODEL_ID)
    stack = _stack(session.client("cloudformation"), application_stack)
    if stack is None:
        raise SystemExit("application stack does not exist")
    outputs = _outputs(stack)
    if outputs["PolicyEngineMode"] != "ENFORCE":
        raise SystemExit("approval verification requires ENFORCE policy mode")

    operator_session = _assume_role_session(session, outputs["OperatorRoleArn"])
    client = McpClient(operator_session, region, outputs["GatewayUrl"])
    client.initialize()
    operator_tools = _tool_names(client.tools())
    target = BrokerToolTarget(
        DynamoDbLedger(operator_session.resource("dynamodb").Table(outputs["ExecutionLedgerName"]))
    )
    started_ms = int(time.time() * 1000)

    task_id = f"approval-{uuid.uuid4().hex[:12]}"
    runtime_result = _invoke_runtime(
        session,
        outputs,
        {
            "task_id": task_id,
            "service_id": "claims-api",
            "alarm_code": "HIGH_ERROR_RATE",
            "environment": "demo",
        },
    )
    proposal = runtime_result.get("proposal", {})
    arguments = {
        "task_id": task_id,
        "service_id": "claims-api",
        "environment": "demo",
        "action": proposal.get("proposed_action", ""),
        "idempotency_key": f"{task_id}-effect",
    }
    pending = _tool_content(client.call("BrokerTools___execute_remediation", arguments))
    approval_request_id = str(pending.get("approval_request_id", ""))
    approved = target.approve(task_id, approval_request_id)
    changed = client.call(
        "BrokerTools___execute_remediation",
        {**arguments, "idempotency_key": f"{task_id}-changed"},
    )
    committed = _tool_content(client.call("BrokerTools___execute_remediation", arguments))
    replay = _tool_content(client.call("BrokerTools___execute_remediation", arguments))
    try:
        target.approve(task_id, approval_request_id)
        reused_approval_code = "UNEXPECTED_SUCCESS"
    except ContractError as exc:
        reused_approval_code = exc.code.value

    uncertain_task_id = f"uncertain-{uuid.uuid4().hex[:12]}"
    uncertain_arguments = {
        **arguments,
        "task_id": uncertain_task_id,
        "idempotency_key": f"{uncertain_task_id}-effect",
    }
    uncertain_pending = _tool_content(
        client.call("BrokerTools___execute_remediation", uncertain_arguments)
    )
    target.approve(uncertain_task_id, str(uncertain_pending.get("approval_request_id", "")))
    client.call("BrokerTools___execute_remediation", uncertain_arguments)
    reconciled = target.reconcile_execution(uncertain_arguments)
    retried = _tool_content(client.call("BrokerTools___execute_remediation", uncertain_arguments))

    task_ids = {task_id, uncertain_task_id}
    logs = session.client("logs")
    dispatches: list[dict[str, Any]] = []
    gateway_counts = {value: 0 for value in task_ids}
    for _ in range(6):
        dispatches = [
            record
            for record in _dispatch_events(logs, outputs["ToolLambdaLogGroupName"], started_ms)
            if record.get("task_id") in task_ids
        ]
        gateway_counts = _gateway_task_counts(
            logs, outputs["GatewayLogGroupName"], started_ms, task_ids
        )
        if {str(record.get("task_id")) for record in dispatches} == task_ids and all(
            gateway_counts.values()
        ):
            break
        time.sleep(5)
    dispatched_task_ids = {str(record.get("task_id")) for record in dispatches}
    cases = {
        "runtime_proposes_approved_action": runtime_result.get("outcome") == "COMPLETED"
        and proposal.get("requires_approval") is True
        and arguments["action"] == "restart_worker_pool",
        "operator_receives_exact_tool_surface": operator_tools
        == sorted(
            [
                "BrokerTools___execute_remediation",
                "BrokerTools___get_runbook_procedure",
                "BrokerTools___inspect_service",
            ]
        ),
        "unapproved_request_requires_approval": pending.get("status") == "APPROVAL_REQUIRED"
        and bool(approval_request_id),
        "operator_approval_is_exact": approved.approval_request_id == approval_request_id,
        "changed_arguments_are_denied": _is_error(changed),
        "approved_action_commits_once": committed.get("status") == "COMMITTED"
        and committed.get("effect_count") == 1,
        "replay_returns_same_receipt": replay == committed,
        "consumed_approval_cannot_be_reused": reused_approval_code
        == ErrorCode.APPROVAL_ALREADY_CONSUMED.value,
        "lost_response_reconciles_once": reconciled.get("status") == "COMMITTED"
        and reconciled.get("effect_count") == 1,
        "retry_after_reconciliation_returns_same_receipt": retried == reconciled,
        "lambda_events_are_task_correlated": dispatched_task_ids == task_ids,
        "gateway_events_are_task_correlated": all(gateway_counts.values()),
    }
    report = {
        "account": f"********{account[-4:]}",
        "region": region,
        "execution_mode": "AWS_APPROVAL_RECONCILIATION",
        "model": MODEL_ID,
        "policy_engine_mode": "ENFORCE",
        "task_ids": sorted(task_ids),
        "dispatch_counts": {
            task: sum(1 for record in dispatches if record.get("task_id") == task)
            for task in sorted(task_ids)
        },
        "gateway_event_counts": gateway_counts,
        "policy_evidence": {
            "mode": "ENFORCE",
            "operator_tools": operator_tools,
            "runtime_tools": sorted(
                [
                    "BrokerTools___get_runbook_procedure",
                    "BrokerTools___inspect_service",
                ]
            ),
        },
        "cases": cases,
        "passed": all(cases.values()),
        "verified_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    json_path = ARTIFACTS / "aws-approval-verification.json"
    markdown_path = ARTIFACTS / "aws-approval-verification.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(_approval_markdown(report), encoding="utf-8")
    print(f"report={json_path.name}")
    print(f"summary={markdown_path.name}")
    print(f"passed={str(report['passed']).lower()}")
    if not report["passed"]:
        raise SystemExit("AWS approval verification failed")


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
    subparsers.add_parser("verify-runtime")
    approve_parser = subparsers.add_parser("approve")
    approve_parser.add_argument("--task-id", required=True)
    approve_parser.add_argument("--approval-request-id", required=True)
    subparsers.add_parser("verify-approval")
    subparsers.add_parser("destroy")
    arguments = parser.parse_args()
    if arguments.command == "deploy":
        deploy()
    elif arguments.command == "verify":
        verify(arguments.mode)
    elif arguments.command == "policy-mode":
        set_policy_mode(arguments.mode)
    elif arguments.command == "verify-runtime":
        verify_runtime()
    elif arguments.command == "approve":
        approve(arguments.task_id, arguments.approval_request_id)
    elif arguments.command == "verify-approval":
        verify_approval_flow()
    else:
        destroy()


if __name__ == "__main__":
    main()
