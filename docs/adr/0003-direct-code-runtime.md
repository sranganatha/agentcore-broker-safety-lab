# ADR 0003: Direct-code AgentCore Runtime

## Status

Accepted

## Context

The broker needs one small Python runtime that reads two governed MCP tools and asks Nova Micro for a proposal. The runtime must be reproducible, least-privileged, and simple to remove. It does not need system packages, custom native services, or a long-running local model.

## Decision

Package the pinned Python dependencies and application code as an ARM64-compatible ZIP and deploy it with `AWS::BedrockAgentCore::Runtime` using the managed Python 3.12 direct-code runtime.

The broker deterministically calls the two read tools before model invocation. Nova Micro receives only the validated task and evidence, and returns a proposal that is validated again. The runtime role can invoke the exact Gateway and Nova Micro model. It has no direct Lambda, DynamoDB, or approval mutation permission.

## Alternatives considered

- A custom ECR image provides full operating-system control, but adds an image repository, image scanning and lifecycle work, and a larger release surface that this pure-Python runtime does not need.
- A direct Bedrock API process outside AgentCore is simpler to host, but would not demonstrate the managed Runtime identity, invocation, lifecycle, and observability boundary.
- Giving MCP tools directly to the model would demonstrate autonomous tool selection, but makes call order and call count probabilistic. Deterministic evidence collection is safer and easier to verify for this broker.

## Consequences

- Deployment artifacts must contain Linux ARM64 wheels and stay within AgentCore direct-code size limits.
- Dependency updates require rebuilding and live verification of the ZIP.
- Public networking is used for the managed AWS endpoints. A production workload with private connectivity requirements should evaluate VPC mode and endpoints.
- The 25-second in-process cancellation signal bounds cooperative execution, while the invoking client also applies a 60-second read timeout. A blocking dependency may not stop immediately when the signal is set.
- The model produces proposals only, and the broker code never calls the remediation tool.
- `bedrock-agentcore:InvokeGateway` is scoped to the Gateway resource, not an individual tool. Compromised runtime code could request the remediation tool through that Gateway. The policy still restricts its argument class, and the target still requires an external exact approval before an effect. A production design that requires identity-level separation should use a read-only Gateway or a principal-aware deny policy for the runtime role.
