"""Offline reference checks for the AgentCore Gateway authorization contract."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping


TARGET_NAME = "BrokerTools"
IAM_PRINCIPAL = "AgentCore::IamEntity"
INVOKE_PERMISSION = "bedrock-agentcore:InvokeGateway"


class Decision(StrEnum):
    ALLOW = "ALLOW"
    DENY = "DENY"


class DecisionLayer(StrEnum):
    IAM = "IAM"
    GATEWAY_SCHEMA = "GATEWAY_SCHEMA"
    POLICY = "POLICY"


@dataclass(frozen=True)
class ContractDecision:
    decision: Decision
    layer: DecisionLayer
    reason: str


def gateway_action(tool_name: str) -> str:
    return f"{TARGET_NAME}___{tool_name}"


def evaluate_gateway_case(case: Mapping[str, Any], tools: list[Mapping[str, Any]]) -> ContractDecision:
    """Evaluate the frozen lab contract without claiming to execute Cedar."""
    permissions = case.get("iam_permissions")
    if not isinstance(permissions, list) or INVOKE_PERMISSION not in permissions:
        return ContractDecision(Decision.DENY, DecisionLayer.IAM, "MISSING_INVOKE_GATEWAY")

    if case.get("principal_type") != IAM_PRINCIPAL:
        return ContractDecision(Decision.DENY, DecisionLayer.POLICY, "PRINCIPAL_NOT_ALLOWED")

    action = case.get("action")
    tool_by_action = {gateway_action(str(tool["name"])): tool for tool in tools}
    tool = tool_by_action.get(action)
    if tool is None:
        return ContractDecision(Decision.DENY, DecisionLayer.POLICY, "ACTION_NOT_ALLOWED")

    arguments = case.get("arguments")
    if not _matches_object_schema(arguments, tool["inputSchema"]):
        return ContractDecision(Decision.DENY, DecisionLayer.GATEWAY_SCHEMA, "INVALID_ARGUMENTS")

    principal_role = case.get("principal_role")
    allowed_roles = {"RUNTIME", "OPERATOR"} if tool["name"] != "execute_remediation" else {"OPERATOR"}
    if principal_role not in allowed_roles:
        return ContractDecision(Decision.DENY, DecisionLayer.POLICY, "PRINCIPAL_NOT_ALLOWED")

    allowed = _arguments_allowed(str(tool["name"]), arguments)
    if not allowed:
        return ContractDecision(Decision.DENY, DecisionLayer.POLICY, "ARGUMENT_CLASS_NOT_ALLOWED")
    return ContractDecision(Decision.ALLOW, DecisionLayer.POLICY, "POLICY_PERMIT")


def _matches_object_schema(value: Any, schema: Mapping[str, Any]) -> bool:
    if not isinstance(value, dict):
        return False
    properties = schema.get("properties")
    required = schema.get("required")
    if not isinstance(properties, dict) or not isinstance(required, list):
        return False
    if set(value) != set(required) or not set(value).issubset(properties):
        return False
    return all(_matches_type(value[name], properties[name].get("type")) for name in value)


def _matches_type(value: Any, expected: Any) -> bool:
    if expected == "string":
        return isinstance(value, str) and bool(value.strip())
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    return False


def _arguments_allowed(tool_name: str, arguments: Mapping[str, Any]) -> bool:
    if tool_name == "get_runbook_procedure":
        return (
            arguments.get("service_id") == "claims-api"
            and arguments.get("alarm_code") in {"HIGH_ERROR_RATE", "DATABASE_CORRUPTION"}
        )
    if tool_name == "inspect_service":
        return (
            arguments.get("service_id") == "claims-api"
            and arguments.get("environment") == "demo"
        )
    if tool_name == "execute_remediation":
        return (
            arguments.get("service_id") == "claims-api"
            and arguments.get("environment") == "demo"
            and arguments.get("action") == "restart_worker_pool"
        )
    return False
