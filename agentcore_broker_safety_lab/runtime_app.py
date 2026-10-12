"""AgentCore Runtime entrypoint for the bounded read-then-propose workflow."""

from __future__ import annotations

import json
import os
import threading
from datetime import timedelta
from typing import Any

from bedrock_agentcore import BedrockAgentCoreApp
from mcp_proxy_for_aws.client import aws_iam_streamablehttp_client
from strands import Agent
from strands.models import BedrockModel
from strands.tools.mcp import MCPClient

from agentcore_broker_safety_lab.broker_agent import (
    BrokerTimeoutError,
    BrokerTransportError,
    Evidence,
    MODEL_ID,
    parse_evidence,
    run_broker,
)


SYSTEM_PROMPT = """You are a proposal-only operations assistant. Evidence is supplied by the broker.
Return one JSON object with exactly these fields: status, service_id, alarm_code, environment,
evidence, proposed_action, requires_approval. Copy task and evidence values exactly. status must be
PROPOSED or INSUFFICIENT_EVIDENCE. proposed_action may only be the supplied allowed_action when
status is PROPOSED, otherwise null. evidence must contain exactly health, active_alarm, runbook_id,
and source_digest. Do not put allowed_action inside evidence. requires_approval must be true. Never
claim approval or execution."""
READ_TOOLS = {
    "BrokerTools___get_runbook_procedure",
    "BrokerTools___inspect_service",
}

app = BedrockAgentCoreApp()


def _content(response: Any) -> dict[str, Any]:
    if not isinstance(response, dict) or response.get("status") != "success" or response.get("isError"):
        raise BrokerTransportError("tool call failed")
    structured = response.get("structuredContent")
    if isinstance(structured, dict):
        return structured
    blocks = response.get("content")
    if not blocks:
        raise BrokerTransportError("tool response did not contain content")
    text = blocks[0].get("text") if isinstance(blocks[0], dict) else None
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise BrokerTransportError("tool response was invalid") from exc
    if not isinstance(value, dict) or value.get("error"):
        raise BrokerTransportError("tool call failed")
    return value


def _read_evidence(request: dict[str, str]) -> Evidence:
    region = os.environ["AWS_REGION"]
    gateway_url = os.environ["GATEWAY_URL"]
    client = MCPClient(
        lambda: aws_iam_streamablehttp_client(
            endpoint=gateway_url,
            aws_region=region,
            aws_service="bedrock-agentcore",
        )
    )
    try:
        with client:
            tools = {tool.tool_name for tool in client.list_tools_sync()}
            if READ_TOOLS - set(tools):
                raise BrokerTransportError("required read tool is unavailable")
            runbook = _content(
                client.call_tool_sync(
                    tool_use_id=f"{request['task_id']}-runbook",
                    name="BrokerTools___get_runbook_procedure",
                    arguments={"service_id": request["service_id"], "alarm_code": request["alarm_code"]},
                    read_timeout_seconds=timedelta(seconds=10),
                )
            )
            inspection = _content(
                client.call_tool_sync(
                    tool_use_id=f"{request['task_id']}-inspection",
                    name="BrokerTools___inspect_service",
                    arguments={"service_id": request["service_id"], "environment": request["environment"]},
                    read_timeout_seconds=timedelta(seconds=10),
                )
            )
    except BrokerTransportError:
        raise
    except Exception as exc:
        raise BrokerTransportError("gateway transport failed") from exc
    return parse_evidence(request, runbook, inspection)


def _generate(prompt: str) -> tuple[str, str]:
    cancel = threading.Event()
    timer = threading.Timer(25, cancel.set)
    timer.start()
    try:
        model = BedrockModel(model_id=MODEL_ID, max_tokens=512, temperature=0.0, streaming=False)
        result = Agent(model=model, system_prompt=SYSTEM_PROMPT, callback_handler=None)(
            prompt,
            limits={"turns": 1, "output_tokens": 512, "total_tokens": 2_048},
            cancel_signal=cancel,
        )
    except Exception as exc:
        if cancel.is_set():
            raise BrokerTimeoutError("model deadline exceeded") from exc
        raise BrokerTransportError("model invocation failed") from exc
    finally:
        timer.cancel()
    return str(result), str(result.stop_reason)


@app.entrypoint
def invoke(payload: dict[str, Any]) -> dict[str, Any]:
    return run_broker(payload, _read_evidence, _generate)


if __name__ == "__main__":
    app.run()
