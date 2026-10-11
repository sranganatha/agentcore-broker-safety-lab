# ADR 0002: Separate policy authorization from target safety

- Status: Accepted
- Date: 2026-10-11

## Context

Gateway authorization and safe execution answer different questions. The Gateway can decide whether an authenticated workload may invoke a tool with an allowed argument class. It cannot establish that a human approved one exact operation, guarantee single-use approval, or reconcile an uncertain external effect.

Relying only on target validation would also allow unauthorized requests to reach the Lambda boundary. Relying only on the model or prompt would make authorization nondeterministic.

## Decision

Use AgentCore Policy for Gateway action and argument-class authorization:

- Allow only the three `BrokerTools___*` actions.
- Limit reads to the `claims-api` demo evidence.
- Limit remediation requests to `claims-api`, the `demo` environment, and `restart_worker_pool`.
- Default all unnamed actions and argument classes to deny.

Use the Lambda target and execution ledger for exact domain enforcement:

- Strictly validate every argument and reject unknown fields.
- Bind approval to the complete canonical operation and expiry.
- Keep approval creation and approval writes outside the agent path.
- Use conditional state transitions, stable idempotency keys, and reconciliation to prevent a duplicate demonstrated effect.

Attach the policy engine in `LOG_ONLY` first so the frozen cases can be compared with observed decisions. Target validation remains active during shadow evaluation. Promotion to `ENFORCE` requires matching live evidence and is a later explicit operation.

## Consequences

- A policy allow is necessary but never sufficient for remediation execution.
- Evidence must name the denial source as IAM, Gateway schema, AgentCore Policy, or the Lambda target.
- Policy and target checks intentionally overlap for the allowed remediation class as defense in depth.
- This design does not claim distributed exactly-once delivery. It proves one effect for the demonstrated idempotency and reconciliation protocol.
