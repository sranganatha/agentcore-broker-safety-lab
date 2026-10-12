"""Deterministic account-free demonstration of the broker safety controls."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from .broker_agent import Evidence, parse_evidence, run_broker
from .contracts import ContractError, ErrorCode
from .gateway_contract import evaluate_gateway_case
from .ledger import InMemoryLedger
from .tool_target import BrokerToolTarget
from .validate_fixtures import ROOT


NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
TASK = {
    "task_id": "demo-task-001",
    "service_id": "claims-api",
    "alarm_code": "HIGH_ERROR_RATE",
    "environment": "demo",
}


def _context(tool_name: str) -> Any:
    return SimpleNamespace(
        client_context=SimpleNamespace(
            custom={"bedrockAgentCoreToolName": f"BrokerTools___{tool_name}"}
        )
    )


def _error_code(operation: Callable[[], Any]) -> str:
    try:
        operation()
    except ContractError as exc:
        return exc.code.value
    return "UNEXPECTED_SUCCESS"


def _proposal(evidence: Evidence) -> str:
    return json.dumps(
        {
            "status": "PROPOSED",
            "service_id": TASK["service_id"],
            "alarm_code": TASK["alarm_code"],
            "environment": TASK["environment"],
            "evidence": {
                "health": evidence.health,
                "active_alarm": evidence.active_alarm,
                "runbook_id": evidence.runbook_id,
                "source_digest": evidence.source_digest,
            },
            "proposed_action": evidence.allowed_action,
            "requires_approval": True,
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def run_demo() -> dict[str, Any]:
    ledger = InMemoryLedger()
    target = BrokerToolTarget(ledger)

    def read_evidence(request: dict[str, str]) -> Evidence:
        runbook = target.handle(
            {
                "task_id": request["task_id"],
                "service_id": request["service_id"],
                "alarm_code": request["alarm_code"],
            },
            _context("get_runbook_procedure"),
            now=NOW,
        )
        inspection = target.handle(
            {
                "task_id": request["task_id"],
                "service_id": request["service_id"],
                "environment": request["environment"],
            },
            _context("inspect_service"),
            now=NOW,
        )
        return parse_evidence(request, runbook, inspection)

    evidence = read_evidence(TASK)
    broker = run_broker(TASK, lambda _: evidence, lambda _: (_proposal(evidence), "end_turn"))
    action = {
        "task_id": TASK["task_id"],
        "service_id": TASK["service_id"],
        "environment": TASK["environment"],
        "action": broker.get("proposal", {}).get("proposed_action", ""),
        "idempotency_key": "demo-effect-001",
    }
    execute_context = _context("execute_remediation")
    pending = target.handle(action, execute_context, now=NOW)
    approval_id = str(pending.get("approval_request_id", ""))
    approved = target.approve(TASK["task_id"], approval_id, now=NOW)
    changed_code = _error_code(
        lambda: target.handle(
            {**action, "idempotency_key": "demo-effect-changed"}, execute_context, now=NOW
        )
    )
    committed = target.handle(action, execute_context, now=NOW)
    replay = target.handle(action, execute_context, now=NOW)
    consumed_code = _error_code(
        lambda: target.approve(TASK["task_id"], approval_id, now=NOW)
    )

    uncertain = {
        **action,
        "task_id": "demo-task-uncertain",
        "idempotency_key": "demo-effect-uncertain",
    }
    uncertain_pending = target.handle(uncertain, execute_context, now=NOW)
    target.approve(
        uncertain["task_id"], str(uncertain_pending.get("approval_request_id", "")), now=NOW
    )
    uncertain_code = _error_code(
        lambda: target.handle(
            uncertain, execute_context, now=NOW, lose_response_after_commit=True
        )
    )
    reconciled = target.reconcile_execution(uncertain, now=NOW)
    retried = target.handle(uncertain, execute_context, now=NOW)

    tools = json.loads((ROOT / "config" / "tools.json").read_text(encoding="utf-8"))
    policy_cases = json.loads(
        (ROOT / "config" / "gateway_policy_cases.json").read_text(encoding="utf-8")
    )
    policy_matches = all(
        (decision := evaluate_gateway_case(case, tools)).decision == case["expected_decision"]
        and decision.layer == case["expected_layer"]
        and decision.reason == case["expected_reason"]
        for case in policy_cases
    )

    cases = {
        "offline_policy_corpus_matches": policy_matches,
        "fixture_model_proposes_allowed_action": broker.get("outcome") == "COMPLETED"
        and action["action"] == "restart_worker_pool",
        "fixture_model_does_not_write": broker.get("write_tool_calls") == 0,
        "unapproved_action_requires_approval": pending.get("status")
        == ErrorCode.APPROVAL_REQUIRED.value,
        "operator_approval_is_exact": approved.approval_request_id == approval_id,
        "changed_arguments_are_denied": changed_code
        == ErrorCode.APPROVAL_ARGUMENT_MISMATCH.value,
        "approved_action_commits_once": committed.get("status") == "COMMITTED"
        and committed.get("effect_count") == 1,
        "replay_returns_same_receipt": replay == committed,
        "consumed_approval_is_denied": consumed_code
        == ErrorCode.APPROVAL_ALREADY_CONSUMED.value,
        "lost_response_is_uncertain": uncertain_code == ErrorCode.OUTCOME_UNKNOWN.value,
        "reconciliation_returns_one_effect": reconciled.get("status") == "COMMITTED"
        and reconciled.get("effect_count") == 1,
        "retry_after_reconciliation_returns_same_receipt": retried == reconciled,
    }
    return {
        "schema_version": 1,
        "execution_mode": "FIXTURE",
        "model": "deterministic-fixture",
        "network_required": False,
        "cloud_calls": 0,
        "policy_case_count": len(policy_cases),
        "cases": cases,
        "passed": all(cases.values()),
    }


def _markdown(report: dict[str, Any]) -> str:
    rows = ["| Case | Result |", "|---|---|"]
    rows.extend(
        f"| {name} | {'PASS' if passed else 'FAIL'} |"
        for name, passed in report["cases"].items()
    )
    return "\n".join(
        [
            "# Offline broker safety demonstration",
            "",
            "This report uses deterministic fixtures. It does not claim live model or AWS execution.",
            "",
            f"- Execution mode: {report['execution_mode']}",
            f"- Model: {report['model']}",
            f"- Network required: {str(report['network_required']).lower()}",
            f"- Cloud calls: {report['cloud_calls']}",
            f"- Policy cases: {report['policy_case_count']}",
            f"- Result: {'PASS' if report['passed'] else 'FAIL'}",
            "",
            *rows,
            "",
        ]
    )


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    arguments = parser.parse_args()
    report = run_demo()
    arguments.output.mkdir(parents=True, exist_ok=True)
    (arguments.output / "offline-report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (arguments.output / "offline-report.md").write_text(_markdown(report), encoding="utf-8")
    for name, passed in report["cases"].items():
        print(f"{'PASS' if passed else 'FAIL'} {name}")
    if not report["passed"]:
        raise SystemExit("offline demonstration failed")


if __name__ == "__main__":
    main()
