# AgentCore Broker Safety Lab

A policy-controlled agent broker that turns synthetic operational evidence into approved, retry-safe actions.

> Model output can propose an action. Policy, approval, and execution safety must be enforced outside the model.

## Why this exists

An agent that can call state-changing tools needs stronger controls than a careful prompt. The broker must restrict the available capability surface, validate exact arguments, keep approval outside the agent path, and handle a lost response without assuming the write failed.

This project targets one inspectable workflow:

```text
Synthetic service alarm
    -> Nova Micro agent in AgentCore Runtime
    -> AgentCore Gateway tools
    -> AgentCore Policy decision
    -> exact approval check
    -> idempotent execution receipt
    -> reconciliation after an uncertain outcome
```

The AWS-backed workflow will use one AgentCore Runtime, one Gateway, one policy engine, and one Lambda target exposing three tools:

| Tool | Risk | Purpose |
|---|---:|---|
| `get_runbook_procedure` | Low | Return the applicable synthetic procedure and source digest |
| `inspect_service` | Low | Return deterministic synthetic health and alarm state |
| `execute_remediation` | High | Require exact approval and return one execution receipt |

## Implemented foundation

The repository currently provides the account-free contract layer for that workflow:

- Strict broker-task, approval-request, and execution-receipt contracts
- Stable machine-readable error codes
- Deterministic canonical action hashing
- AgentCore-compatible JSON tool definitions
- Exact Gateway-visible MCP action names and argument schemas
- Explicit Cedar policy for the one synthetic service and remediation class
- Frozen offline authorization cases across IAM, schema, and policy denial layers
- Reviewable CloudFormation for the IAM-authenticated Gateway, Lambda target, policy engine, scoped execution role, and log delivery
- Policy engine attachment in observation-only `LOG_ONLY` mode
- Original synthetic service and runbook fixtures
- Lambda-compatible dispatch for the three validated tools
- Exact, expiring approval state outside the public tool surface
- Conditional in-memory and DynamoDB execution ledgers
- Post-commit response-loss injection and receipt reconciliation
- Boundary and fixture tests that run without a cloud account
- A Podman-only validation path with no host Python packages

The offline authorization cases are a reference contract, not a Cedar runtime emulator. The CloudFormation template is linted locally but has not yet been deployed by this repository. Cloud resources and model invocation are not represented by fixture output. They will be verified separately and labeled as AWS execution evidence when implemented.

## Security properties

- Approval identity binds the task, tool, and complete normalized arguments.
- Argument key order does not change an action identity.
- Changed arguments, non-JSON values, non-finite numbers, and non-string object keys fail before hashing.
- Receipt states enforce their required effect count and provider-operation fields.
- Tool schemas expose only the three named lab capabilities.
- Gateway policy permits only the `claims-api` demo reads and `restart_worker_pool` remediation class.
- Unknown actions, wrong principal types, missing invoke permission, extra fields, and malformed argument types have frozen deny cases.
- Repeated and concurrent idempotency keys resolve to one committed synthetic effect.
- An uncertain post-commit result is reconciled before another execution attempt.
- Fixtures contain no real endpoint, account, customer, or production data.

## Scope

This is a bounded safety lab, not a general agent framework, production incident platform, authorization service, or compliance-certified system. The target MVP governs an AgentCore workload identity. It does not implement end-user OAuth delegation, AgentCore Memory, multiple agents, A2A, a browser UI, a vector database, provider routing, or real infrastructure changes.

## Local validation

Clean validation requires Git, Make, and a running Podman machine. No host Python dependency is installed or required.

```bash
git clone https://github.com/sranganatha/agentcore-broker-safety-lab.git
cd agentcore-broker-safety-lab
podman info
make test-container
```

The tooling image validates source compilation, contract drift, and CloudFormation structure. The application image then runs the complete account-free test suite. All validation containers run with networking disabled, and the host receives no Python packages.

## Design references

- [MVP specification](docs/mvp-spec.md)
- [Workload identity boundary](docs/adr/0001-workload-identity-boundary.md)
- [Policy and target enforcement](docs/adr/0002-policy-and-target-enforcement.md)
- [Repository rules](AGENTS.md)
