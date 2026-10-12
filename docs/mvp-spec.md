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

## 10. Architecture tradeoffs

These are bounded MVP choices, not universal recommendations. Managed-service claims remain unproven until the live AWS cases pass.

| Choice | Alternative considered | Why this MVP chooses it | Consequence |
|---|---|---|---|
| AgentCore Runtime with Nova Micro | Call Bedrock directly from an application-owned runtime | The project must exercise AgentCore workload identity, isolation, and managed execution rather than simulate them | More AWS coupling and operational surface than a direct Bedrock API application needs |
| AgentCore Gateway as the MCP boundary | Let the agent call Lambda or application APIs directly | Gateway makes tool discovery, authentication, policy evaluation, and dispatch visible at one governed boundary | Adds a managed hop, service quotas, and schema synchronization work |
| AgentCore Policy plus Lambda validation | Rely only on policy, target validation, or prompting | Policy controls workload capabilities while the target enforces exact domain state, approval, and replay rules that a model must not control | Similar constraints exist at two layers and must be tested for drift |
| IAM workload identity | Propagate an end-user OAuth identity | One non-human identity is sufficient to demonstrate least privilege without introducing an identity product into the lab | The MVP cannot make per-user authorization or delegation claims |
| One Lambda target with three tools | Deploy one target per tool or a general broker service | One target keeps packaging, permissions, teardown, and local dispatch tests inspectable for a three-tool surface | The shared target would become an isolation and ownership concern if the tool set grew |
| DynamoDB conditional writes | Use a workflow engine, relational transaction, or queue | Conditional writes fit the exact-approval and idempotency ledger while keeping one authoritative state store | The proof applies only to the modeled ledger effect and is not general exactly-once delivery |
| CloudFormation | Use CDK, Terraform, or imperative SDK deployment | Native resources keep the deployed architecture explicit without adding a synthesis framework or second language | New AgentCore features may reach CloudFormation late and require a narrowly documented exception |
| CloudWatch-correlated evidence | Adopt a separate observability platform | Native service telemetry provides the shortest path to correlate Runtime, Gateway, Policy, Lambda, and DynamoDB activity | Retention, ingestion cost, payload redaction, and cross-account operations remain deployment responsibilities |
| Synthetic service action | Modify a real operational system | A deterministic target permits denial, replay, and response-loss testing without risking infrastructure | Results demonstrate control behavior, not readiness for a real provider's failure and idempotency semantics |

The two lasting security decisions receive ADRs only when their AWS paths are implemented: workload identity boundaries, and the split between policy and target enforcement.

## 11. Known risks and limitations

### Current foundation

The repository currently proves strict contracts, canonical action identity, fixture integrity, validated local tool dispatch, exact approval transitions, concurrent in-memory idempotency, and response-loss reconciliation. It includes the conditional DynamoDB request boundary, but has not exercised it against a deployed table. It does not yet prove that AgentCore authentication, Gateway dispatch, Cedar evaluation, deployed Lambda and DynamoDB behavior, or CloudWatch correlation works as designed.

- A SHA-256 action digest binds normalized content but does not authenticate an approver. The implemented operator identity and DynamoDB authorization path must supply that trust.
- Offline tool definitions can drift from the deployed Gateway schema or Cedar policy. Deployment verification compares the live capability surface and policy behavior with the repository contract.
- Deterministic fixtures cover declared cases, not unknown model behavior or managed-service failures. Fixture results must never be presented as AWS evidence.
- AgentCore APIs, regional support, quotas, and CloudFormation coverage may change while the project is developed. Deployment preflight and pinned evidence must record the versions and region actually tested.

### Residual runtime risks

| Risk | MVP control | Residual limitation |
|---|---|---|
| Prompt injection or malformed model output requests an unsafe tool call | Gateway policy and strict target validation ignore model authority | The finite policy and test corpus cannot prove all future inputs safe |
| Policy is incomplete while running in shadow mode | Target validation remains active and state-changing negative cases are bounded | Shadow mode itself does not block a request |
| Policy, Gateway schema, and target rules diverge | Frozen allow and deny cases run in shadow and enforcement modes | Configuration changes outside the reviewed deployment can reintroduce drift |
| Approval is changed, expired, raced, or replayed | Exact digest binding, expiry checks, single-use state, and conditional writes | Correctness depends on the final DynamoDB key and transaction design passing concurrent tests |
| A response is lost after commit | Return an unknown outcome and reconcile by stable idempotency key before retry | A real downstream provider also needs a durable operation identifier or native idempotency support |
| Runtime role gains excessive permissions | Separate runtime, target, and operator IAM roles with negative permission assertions | The lab does not implement enterprise access review or continuous entitlement monitoring |
| Telemetry exposes sensitive input or identifiers | Structured redaction tests and sanitized committed evidence | CloudWatch configuration, retention, and operator queries can still mishandle data |
| Throttling, cold starts, regional failure, or quota exhaustion interrupts the workflow | Bounded timeouts, visible failures, and no silent fixture or model fallback | The MVP has no multi-region recovery, queue, backpressure, or availability SLO |
| Model output varies between runs | Deterministic offline contracts plus a bounded live verification corpus | One model and a small corpus do not establish broad behavioral reliability |
| Resources create unexpected cost or remain deployed | Fixed call budgets, explicit Make targets, exact stack names, and teardown verification | Pricing changes and failed cleanup still require operator monitoring |

## 12. Production improvements outside the MVP

A production broker would preserve the same trust boundaries while adding only requirements justified by its environment:

- Propagate an authenticated end-user or service principal and evaluate both caller and workload authorization.
- Integrate external actions only when their provider supports durable operation lookup, native idempotency, or a compensating workflow.
- Add queueing, backpressure, dead-letter handling, concurrency limits, and explicit service-level objectives.
- Separate tools or targets when ownership, data sensitivity, scaling, or blast radius differs.
- Add multi-account deployment, recovery objectives, policy change controls, access reviews, and centralized security monitoring.
- Expand adversarial evaluation, policy mutation tests, load tests, model-change gates, and rollback evidence.
- Define telemetry classification, sampling, retention, deletion, and cost budgets before processing sensitive data.
- Reassess direct Bedrock APIs or another runtime if AgentCore no longer provides enough operational value to justify its coupling.
