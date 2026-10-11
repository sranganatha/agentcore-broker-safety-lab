"""Validate bundled tool and synthetic-domain fixtures without external services."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any

from .contracts import BrokerTask, ContractError, ErrorCode
from .gateway_contract import evaluate_gateway_case, gateway_action


ROOT = Path(__file__).resolve().parent.parent
EXPECTED_TOOLS = {"get_runbook_procedure", "inspect_service", "execute_remediation"}


def _load_json(name: str) -> Any:
    path = ROOT / "config" / name
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(ErrorCode.INVALID_REQUEST, f"cannot load {name}") from exc


def _validate_schema(schema: Any, label: str) -> None:
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{label} must be an object schema")
    properties = schema.get("properties")
    required = schema.get("required", [])
    if not isinstance(properties, dict) or not properties:
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{label} must define properties")
    if not isinstance(required, list) or not set(required).issubset(properties):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{label} has invalid required properties")
    if schema.get("additionalProperties") is not False:
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{label} must reject additional properties")


def validate_tools() -> None:
    tools = _load_json("tools.json")
    if not isinstance(tools, list):
        raise ContractError(ErrorCode.INVALID_REQUEST, "tools.json must contain a list")
    names = [tool.get("name") for tool in tools if isinstance(tool, dict)]
    if len(tools) != 3 or set(names) != EXPECTED_TOOLS or len(names) != len(set(names)):
        raise ContractError(ErrorCode.INVALID_REQUEST, "tools.json must define the three unique lab tools")
    for tool in tools:
        if set(tool) != {"name", "description", "inputSchema", "outputSchema"}:
            raise ContractError(ErrorCode.INVALID_REQUEST, f"tool {tool.get('name')} has unexpected fields")
        if not isinstance(tool["description"], str) or not tool["description"].strip():
            raise ContractError(ErrorCode.INVALID_REQUEST, f"tool {tool['name']} needs a description")
        _validate_schema(tool["inputSchema"], f"{tool['name']} inputSchema")
        _validate_schema(tool["outputSchema"], f"{tool['name']} outputSchema")


def validate_runbooks() -> dict[tuple[str, str], tuple[str, str]]:
    runbooks = _load_json("synthetic_runbooks.json")
    if not isinstance(runbooks, list) or len(runbooks) != 2:
        raise ContractError(ErrorCode.INVALID_REQUEST, "synthetic_runbooks.json must contain two runbooks")
    identities: dict[tuple[str, str], tuple[str, str]] = {}
    for runbook in runbooks:
        expected = {"runbook_id", "service_id", "alarm_code", "allowed_action", "procedure", "source_digest"}
        if not isinstance(runbook, dict) or set(runbook) != expected:
            raise ContractError(ErrorCode.INVALID_REQUEST, "runbook fields do not match the contract")
        identity = (runbook["service_id"], runbook["alarm_code"])
        if identity in identities:
            raise ContractError(ErrorCode.INVALID_REQUEST, "runbook service and alarm identity must be unique")
        identities[identity] = (runbook["runbook_id"], runbook["allowed_action"])
        digest = hashlib.sha256(runbook["procedure"].encode("utf-8")).hexdigest()
        if digest != runbook["source_digest"] or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise ContractError(ErrorCode.INVALID_REQUEST, f"runbook {runbook['runbook_id']} digest does not match")
    return identities


def validate_services(runbook_identities: dict[tuple[str, str], tuple[str, str]]) -> set[tuple[str, str]]:
    services = _load_json("synthetic_services.json")
    if not isinstance(services, list) or len(services) != 1:
        raise ContractError(ErrorCode.INVALID_REQUEST, "synthetic_services.json must contain one service")
    identities: set[tuple[str, str]] = set()
    expected = {"service_id", "environment", "health", "active_alarm"}
    for service in services:
        if not isinstance(service, dict) or set(service) != expected:
            raise ContractError(ErrorCode.INVALID_REQUEST, "synthetic service fields do not match the contract")
        if any(not isinstance(service[field], str) or not service[field].strip() for field in expected):
            raise ContractError(ErrorCode.INVALID_REQUEST, "synthetic service fields must be non-empty strings")
        identity = (service["service_id"], service["environment"])
        if identity in identities:
            raise ContractError(ErrorCode.INVALID_REQUEST, "synthetic service identity must be unique")
        if (service["service_id"], service["active_alarm"]) not in runbook_identities:
            raise ContractError(ErrorCode.INVALID_REQUEST, "synthetic service alarm must have a runbook")
        identities.add(identity)
    return identities


def validate_cases(runbook_identities: dict[tuple[str, str], tuple[str, str]]) -> None:
    cases = _load_json("cases.json")
    if not isinstance(cases, list) or len(cases) != 2:
        raise ContractError(ErrorCode.INVALID_REQUEST, "cases.json must contain two cases")
    case_ids: set[str] = set()
    for case in cases:
        if not isinstance(case, dict) or set(case) != {"case_id", "task", "expected_runbook", "expected_action"}:
            raise ContractError(ErrorCode.INVALID_REQUEST, "case fields do not match the contract")
        if case["case_id"] in case_ids:
            raise ContractError(ErrorCode.INVALID_REQUEST, "case IDs must be unique")
        case_ids.add(case["case_id"])
        task = BrokerTask.from_mapping(case["task"])
        identity = (task.service_id, task.alarm_code)
        if identity not in runbook_identities:
            raise ContractError(ErrorCode.INVALID_REQUEST, f"case {case['case_id']} has no matching runbook")
        expected_runbook, expected_action = runbook_identities[identity]
        if case["expected_runbook"] != expected_runbook or case["expected_action"] != expected_action:
            raise ContractError(ErrorCode.INVALID_REQUEST, f"case {case['case_id']} expectation does not match its runbook")


def validate_gateway_contract() -> None:
    tools = _load_json("tools.json")
    cases = _load_json("gateway_policy_cases.json")
    if not isinstance(cases, list) or not cases:
        raise ContractError(ErrorCode.INVALID_REQUEST, "gateway_policy_cases.json must contain cases")
    case_ids: set[str] = set()
    for case in cases:
        required = {
            "case_id",
            "principal_type",
            "iam_permissions",
            "action",
            "arguments",
            "expected_decision",
            "expected_layer",
            "expected_reason",
        }
        if not isinstance(case, dict) or set(case) != required:
            raise ContractError(ErrorCode.INVALID_REQUEST, "gateway policy case fields do not match the contract")
        if case["case_id"] in case_ids:
            raise ContractError(ErrorCode.INVALID_REQUEST, "gateway policy case IDs must be unique")
        case_ids.add(case["case_id"])
        actual = evaluate_gateway_case(case, tools)
        expected = (case["expected_decision"], case["expected_layer"], case["expected_reason"])
        if (actual.decision, actual.layer, actual.reason) != expected:
            raise ContractError(ErrorCode.INVALID_REQUEST, f"gateway case {case['case_id']} expectation does not match")

    try:
        template = json.loads((ROOT / "infra" / "application.json").read_text(encoding="utf-8"))
        cedar = (ROOT / "policies" / "broker.cedar").read_text(encoding="utf-8")
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(ErrorCode.INVALID_REQUEST, "cannot load Gateway infrastructure contract") from exc

    resources = template.get("Resources", {})
    gateway = resources.get("BrokerGateway", {}).get("Properties", {})
    if gateway.get("AuthorizerType") != "AWS_IAM":
        raise ContractError(ErrorCode.INVALID_REQUEST, "Gateway must use AWS_IAM authorization")
    if gateway.get("PolicyEngineConfiguration", {}).get("Mode") != "LOG_ONLY":
        raise ContractError(ErrorCode.INVALID_REQUEST, "Gateway policy engine must begin in LOG_ONLY mode")

    target = resources.get("BrokerToolTarget", {}).get("Properties", {})
    if target.get("Name") != "BrokerTools":
        raise ContractError(ErrorCode.INVALID_REQUEST, "Gateway target name must stay BrokerTools")
    inline_tools = target.get("TargetConfiguration", {}).get("Mcp", {}).get("Lambda", {}).get("ToolSchema", {}).get("InlinePayload")
    if not isinstance(inline_tools, list):
        raise ContractError(ErrorCode.INVALID_REQUEST, "Gateway target must define inline tools")
    by_name = {tool["name"]: tool for tool in tools}
    if {tool.get("Name") for tool in inline_tools} != set(by_name):
        raise ContractError(ErrorCode.INVALID_REQUEST, "CloudFormation and manifest tool names differ")
    for inline_tool in inline_tools:
        manifest_tool = by_name[inline_tool["Name"]]
        if inline_tool.get("Description") != manifest_tool["description"]:
            raise ContractError(ErrorCode.INVALID_REQUEST, f"{inline_tool['Name']} descriptions differ")
        for inline_key, manifest_key in (("InputSchema", "inputSchema"), ("OutputSchema", "outputSchema")):
            if _normalize_cfn_schema(inline_tool.get(inline_key)) != _manifest_schema(manifest_tool[manifest_key]):
                raise ContractError(ErrorCode.INVALID_REQUEST, f"{inline_tool['Name']} {manifest_key} differs")

    policy_resource = resources.get("BrokerPolicy", {})
    if policy_resource.get("DependsOn") != "BrokerToolTarget":
        raise ContractError(ErrorCode.INVALID_REQUEST, "policy creation must wait for the Gateway tool schema")
    statement = policy_resource.get("Properties", {}).get("Definition", {}).get("Cedar", {}).get("Statement", {})
    statement_value = statement.get("Fn::Sub") if isinstance(statement, dict) else None
    if not isinstance(statement_value, list) or statement_value[0] != cedar:
        raise ContractError(ErrorCode.INVALID_REQUEST, "CloudFormation and broker.cedar policy statements differ")
    for tool in tools:
        if cedar.count(f'AgentCore::Action::"{gateway_action(tool["name"])}"') != 1:
            raise ContractError(ErrorCode.INVALID_REQUEST, f"Cedar policy must name {tool['name']} exactly once")


def _manifest_schema(schema: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in schema.items() if key != "additionalProperties"}


def _normalize_cfn_schema(schema: Any) -> dict[str, Any]:
    if not isinstance(schema, dict):
        return {}
    normalized: dict[str, Any] = {"type": schema.get("Type")}
    if "Properties" in schema:
        normalized["properties"] = {
            name: _normalize_cfn_schema(value) for name, value in schema["Properties"].items()
        }
    if "Required" in schema:
        normalized["required"] = schema["Required"]
    if "Items" in schema:
        normalized["items"] = _normalize_cfn_schema(schema["Items"])
    if "Description" in schema:
        normalized["description"] = schema["Description"]
    return normalized


def validate_all() -> None:
    validate_tools()
    runbooks = validate_runbooks()
    validate_services(runbooks)
    validate_cases(runbooks)
    validate_gateway_contract()


if __name__ == "__main__":
    validate_all()
    print("validated tools, synthetic fixtures, Gateway schema, policy cases, and infrastructure contract")
