# ADR 0001: Govern the Runtime workload identity

- Status: Accepted
- Date: 2026-10-11

## Context

The broker needs an authenticated caller identity at the Gateway boundary. The MVP has one AgentCore Runtime workload and no end-user sign-in or delegated identity flow. Treating the model prompt or a caller-supplied user identifier as an authorization identity would cross the trust boundary without verification.

## Decision

Use AgentCore Gateway `AWS_IAM` inbound authorization and Cedar's `AgentCore::IamEntity` principal type. IAM controls whether the workload may invoke the Gateway. AgentCore Policy then limits that authenticated workload to the three declared tool actions and the synthetic service argument classes.

The MVP makes workload-level authorization claims only. It does not claim to authorize a human user, preserve an end-user identity through the agent, or provide tenant isolation.

## Consequences

- Runtime requests must be signed with the Runtime execution role credentials.
- The Runtime role will receive `bedrock-agentcore:InvokeGateway` only for this Gateway and will not receive approval-write permission.
- Offline cases distinguish missing IAM invoke permission from a Cedar policy denial.
- Per-user authorization would require a trusted user authentication flow, verified claims, identity propagation, and policies scoped to those claims. That remains outside this MVP.
