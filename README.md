# AgentCore Broker Safety Lab

A policy-controlled agent broker that turns synthetic operational evidence into approved, retry-safe actions.

> Model output can propose an action. Policy, approval, and execution safety must be enforced outside the model.

## Why this exists

An agent that can call state-changing tools needs stronger controls than a careful prompt. The broker must restrict the available capability surface, validate exact arguments, keep approval outside the agent path, and handle a lost response without assuming the write failed.

This project targets one inspectable workflow:

```text
Synthetic service alarm
    -> agent or signed MCP client
    -> AgentCore Gateway tools
    -> AgentCore Policy decision
    -> exact approval check
    -> idempotent execution receipt
    -> reconciliation after an uncertain outcome
```

The implemented AWS boundary uses one AgentCore Runtime with Nova Micro, one AgentCore Gateway, one policy engine, and one Lambda target exposing three tools:

| Tool | Risk | Purpose |
|---|---:|---|
| `get_runbook_procedure` | Low | Return the applicable synthetic procedure and source digest |
| `inspect_service` | Low | Return deterministic synthetic health and alarm state |
| `execute_remediation` | High | Require exact approval and return one execution receipt |

## Implemented boundary

The repository provides the deployable policy and execution boundary for that workflow:

- Strict broker-task, approval-request, and execution-receipt contracts
- Stable machine-readable error codes
- Deterministic canonical action hashing
- AgentCore-compatible JSON tool definitions
- Exact Gateway-visible MCP action names and argument schemas
- Explicit Cedar policy for the one synthetic service and remediation class
- Principal-scoped Cedar policy that gives Runtime only the two read tools and reserves remediation for a separate operator role
- Frozen offline authorization cases across IAM, schema, and policy denial layers
- Reviewable CloudFormation for the IAM-authenticated Gateway, Lambda target, policy engine, scoped execution role, and log delivery
- Policy engine attachment in observation-only `LOG_ONLY` mode
- Original synthetic service and runbook fixtures
- Lambda-compatible dispatch for the three validated tools
- Exact, expiring approval state outside the public tool surface
- Conditional in-memory and DynamoDB execution ledgers
- Post-commit response-loss injection and receipt reconciliation
- Boundary and fixture tests that run without a cloud account
- Signed MCP verification in policy shadow and enforcement modes
- Bounded CloudFormation deployment and teardown through Make and Podman
- Direct-code AgentCore Runtime packaging with pinned Linux ARM64 dependencies
- Deterministic read-before-reasoning workflow with strict task, evidence, and proposal validation
- Nova Micro proposal generation bounded to one turn, 512 output tokens, 2,048 total tokens, and a cooperative 25-second deadline
- A runtime execution role limited to the exact Gateway, Nova Micro, its deployment artifact, and runtime logs
- A trusted operator role limited to the exact Gateway plus approval and reconciliation updates in the one ledger table
- Bounded AWS approval verification covering changed arguments, replay, simulated response loss, and reconciliation
- A Podman-only validation path with no host Python packages

The offline authorization cases are a reference contract, not a Cedar runtime emulator. The Gateway verifier exercises the deployed Gateway and Lambda boundary directly. The separate Runtime verifier invokes Nova Micro and proves that both reads reached the target once, evidence was returned before a proposal, approval remained required, and remediation was not dispatched. Verification reports contain redacted account metadata and bounded pass/fail evidence under the ignored `artifacts/` directory.

## Security properties

- Approval identity binds the task, tool, and complete normalized arguments.
- Argument key order does not change an action identity.
- Changed arguments, non-JSON values, non-finite numbers, and non-string object keys fail before hashing.
- Receipt states enforce their required effect count and provider-operation fields.
- Tool schemas expose only the three named lab capabilities.
- Gateway policy permits only the `claims-api` demo reads and `restart_worker_pool` remediation class.
- Runtime receives exactly the two read tools; only the separate operator identity can reach remediation.
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

## AWS validation

An authenticated non-root IAM or IAM Identity Center session, Make, and a running Podman machine are required. The deployment starts with policy evaluation in `LOG_ONLY`, verifies the live MCP boundary, then requires an explicit promotion before enforcement verification.

```bash
make deploy-aws CONFIRM_DEPLOY=agentcore-broker-safety-lab
make verify-aws
make policy-enforce CONFIRM_POLICY_ENFORCE=agentcore-broker-safety-lab
make verify-aws-enforce
make verify-runtime-aws
make verify-approval-aws
make destroy-aws CONFIRM_DESTROY=agentcore-broker-safety-lab
```

To approve one pending request outside the agent path, provide the exact identifiers returned by the remediation request:

```bash
make approve-aws TASK_ID=<task-id> APPROVAL_REQUEST_ID=<approval-request-id> CONFIRM_APPROVE=agentcore-broker-safety-lab
```

The commands create only the named application and bootstrap stacks in `us-east-1` by default. Override `AWS_REGION`, `APPLICATION_STACK`, or `BOOTSTRAP_STACK` when needed; stack names must retain the `agentcore-broker-safety-lab` prefix. Teardown removes the application stack, its exact artifact prefix, and the bootstrap stack.

If the non-root credentials are stored under a named profile, add `AWS_PROFILE=<profile>` to each AWS Make command. The profile is forwarded into the tooling container while the AWS configuration remains mounted read-only.

## Design references

- [MVP specification](docs/mvp-spec.md)
- [Workload identity boundary](docs/adr/0001-workload-identity-boundary.md)
- [Policy and target enforcement](docs/adr/0002-policy-and-target-enforcement.md)
- [Direct-code AgentCore Runtime](docs/adr/0003-direct-code-runtime.md)
