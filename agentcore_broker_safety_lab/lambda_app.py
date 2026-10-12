"""AWS Lambda boundary for the AgentCore Gateway tool target."""

from __future__ import annotations

import json
import os
from typing import Any

from .contracts import ContractError
from .ledger import DynamoDbLedger
from .tool_target import BrokerToolTarget, _tool_name


_TARGET: BrokerToolTarget | None = None


def _target() -> BrokerToolTarget:
    global _TARGET
    if _TARGET is None:
        import boto3

        table_name = os.environ.get("LEDGER_TABLE_NAME")
        if not table_name:
            raise RuntimeError("LEDGER_TABLE_NAME is required")
        table = boto3.resource("dynamodb").Table(table_name)
        _TARGET = BrokerToolTarget(DynamoDbLedger(table))
    return _TARGET


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Dispatch one validated Gateway invocation without logging raw arguments."""
    tool_name = "unknown"
    try:
        tool_name = _tool_name(context)
        result = _target().handle(event, context)
        _log("tool_dispatch", tool_name, "SUCCESS")
        return result
    except ContractError as exc:
        _log("tool_dispatch", tool_name, exc.code.value)
        raise RuntimeError(exc.code.value) from exc


def _log(event_type: str, tool_name: str, outcome: str) -> None:
    print(
        json.dumps(
            {"event_type": event_type, "tool_name": tool_name, "outcome": outcome},
            sort_keys=True,
            separators=(",", ":"),
        )
    )
