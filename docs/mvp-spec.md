# AgentCore Broker Safety Lab MVP Specification

## 1. Objective

Demonstrate that an Amazon Bedrock AgentCore workload can investigate one synthetic service alarm through a governed tool surface, request exact approval for a permitted remediation, and recover from a post-commit response loss without duplicating the effect.

The safety claim is narrow:

> For the implemented synthetic target, policy limits gateway actions, exact approval is enforced outside the model, and a stable idempotency key plus reconciliation keeps the demonstrated effect count at one.

The project does not claim general exactly-once delivery, production readiness, or end-user delegated authorization.

## 2. Workflow

```text
Broker task
    -> inspect synthetic service
    -> retrieve applicable synthetic runbook
    -> propose allowed action
    -> request exact approval
    -> trusted operator approves outside agent path
    -> execute with stable idempotency key
    -> persist execution receipt
    -> reconcile uncertain response before retry
```

## 3. Components

| Component | Responsibility |
|---|---|
| AgentCore Runtime | Host one bounded Nova Micro agent |
| AgentCore Gateway | Expose the three MCP tools through one endpoint |
| AgentCore Policy | Authorize gateway actions and argument classes for the workload identity |
| Lambda target | Validate tool name, exact arguments, domain action, approval, and idempotency |
| DynamoDB | Store approval requests, approvals, and execution receipts |
| CloudWatch | Preserve operational metrics and correlated traces |

The local fixture path validates contracts and safety logic but is not evidence of managed AgentCore behavior.

## 4. Tool surface

### `get_runbook_procedure`

Input: service ID and alarm code. Output: exact synthetic procedure, allowed action, and source digest.

### `inspect_service`

Input: service ID and environment. Output: deterministic synthetic health and active alarm.

### `execute_remediation`

Input: task ID, service ID, environment, action, and idempotency key. Output: approval request or execution receipt.

The Lambda target accepts the AgentCore gateway prefix only in the documented `${target_name}___${tool_name}` form and dispatches only the three configured tool names.

## 5. Trust boundaries

- Runtime input and model output are untrusted.
- Gateway authentication establishes the workload identity but does not replace tool policy.
- AgentCore Policy decisions do not replace target-side domain validation.
- Tool descriptions and schemas do not grant approval.
- The runtime role cannot write approval state.
- A trusted operator command writes approval for one exact action digest.
- DynamoDB is authoritative for approval and execution state.
- Logs are diagnostic evidence, not the source of truth for effect count.

## 6. Approval identity

Canonical action identity is SHA-256 over normalized JSON containing:

- Schema version
- Task ID
- Tool name
- Complete tool arguments

Object keys are sorted and JSON is encoded without insignificant whitespace. Non-string object keys, non-JSON values, and non-finite numbers are rejected. Changing any bound value changes the digest.

Approval states are `PENDING`, `APPROVED`, `CONSUMED`, and `EXPIRED`.

## 7. Execution receipt

Receipt states are:

- `COMMITTED`: exactly one effect and a provider operation identifier
- `REJECTED`: zero effects and no provider operation identifier
- `OUTCOME_UNKNOWN`: no claimed effect count until reconciliation

A stable idempotency key identifies one intended effect. A repeated request returns the committed receipt. A timeout after dispatch enters an uncertain path and reconciles the ledger before any retry.

## 8. Required cases

### Account-free cases

1. Equivalent argument key order produces the same action digest.
2. Changed task, tool, or argument produces a different digest.
3. Non-string keys, non-JSON values, and non-finite numbers fail.
4. Missing, extra, blank, or timezone-naive contract fields fail.
5. Committed, rejected, and unknown receipts enforce their state rules.
6. Tool definitions contain exactly the three public tools and reject extra input properties.
7. Synthetic runbook digests match their exact procedure text.
8. Every broker case refers to one applicable runbook.

### Live AWS cases

1. Discover exactly the expected tool surface.
2. Run both read tools through Gateway and Lambda.
3. Deny a fabricated tool name.
4. Deny the disallowed remediation action.
5. Return `APPROVAL_REQUIRED` for the allowed but unapproved action.
6. Deny changed arguments after approval.
7. Commit one approved remediation.
8. Prevent approval replay from creating another effect.
9. Reconcile a post-commit response loss to the existing receipt.
10. Match expected policy results in shadow mode before enforcement.
11. Repeat the frozen policy corpus in enforcement mode.
12. Correlate Runtime, Gateway, Policy, Lambda, and receipt evidence by task ID.

## 9. Completion criteria

- Account-free validation runs through Podman without credentials or network access.
- One bounded Nova Micro agent runs in AgentCore Runtime.
- Runtime uses AgentCore Gateway and AgentCore Policy for all three tools.
- AgentCore Policy is evaluated in shadow mode before promotion to enforcement.
- Runtime cannot approve its own action.
- Changed, expired, and consumed approvals fail with stable codes.
- Post-commit response loss reconciles without a second effect.
- Sanitized fixture and AWS reports remain visibly distinct.
- Cloud deployment and teardown are repeatable through explicit Make targets.

Stop when these criteria pass. Do not add a UI, OAuth, memory, multiple agents, A2A, another model, a vector database, or a real infrastructure integration to the MVP.
