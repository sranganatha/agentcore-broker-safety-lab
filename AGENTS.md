# Repository Instructions

## Goal

Implement only the bounded broker safety lab defined in [docs/mvp-spec.md](docs/mvp-spec.md).

The system must prove that an AgentCore workload receives a governed tool surface, a state-changing action requires exact approval outside the model path, and recovery from an uncertain result does not duplicate the demonstrated effect.

## Scope rules

- Build one synthetic service workflow, one Nova Micro agent, one AgentCore Runtime, one Gateway, one policy engine, one Lambda target, and one DynamoDB table.
- Keep the default tests and demo local, deterministic, credential-free, and runnable with Podman networking disabled.
- Keep live AWS verification separate, explicit, bounded, and clearly labeled.
- Do not add a UI, OAuth, AgentCore Memory, A2A, multiple agents, a vector database, a provider router, Kubernetes, or real infrastructure actions.
- Prefer the Python standard library for domain logic and add only the SDK and agent dependencies required by an implemented cloud boundary.
- Do not create placeholder files, abstractions, commands, or documentation for unimplemented features.

## Security and reliability rules

- Treat task input, model output, tool names, tool arguments, approval identifiers, gateway responses, and persisted records as untrusted.
- AgentCore Policy authorizes gateway actions and argument classes; the Lambda target independently validates exact domain rules.
- The agent runtime role must never receive permission to create or approve approval records.
- Bind approval to the task, tool, complete canonical arguments, expiry, and single-use state.
- Give every state-changing request a stable idempotency key.
- Treat a timeout after dispatch as an uncertain outcome, not proof that the write failed.
- Reconcile the execution ledger before retrying an uncertain write.
- Use DynamoDB conditional writes for the demonstrated single-effect invariant.
- Return stable reason codes and redact credentials, account identifiers, and unnecessary raw payloads.
- Never copy AWS credentials into an image, artifact, fixture, log, or repository file.

## Engineering rules

1. Validate inputs and preconditions first and keep the happy path flat.
2. Use domain names such as `approval_request_id`, `action_digest`, `execution_receipt`, and `effect_count`.
3. Isolate Bedrock, AgentCore, DynamoDB, Lambda, CloudWatch, and file-system boundaries.
4. Translate external values into internal contracts at the boundary.
5. Make invalid states unrepresentable with explicit contracts and state-dependent validation.
6. Separate policy and transition decisions from network, persistence, logging, and report-writing side effects.
7. Preserve original error causes while exposing stable redacted error codes.
8. Use one implementation path and avoid interfaces or factories with one implementation.
9. Keep every branch and pull request focused on one issue.
10. Preserve unrelated work and remove debugging output before completion.

Prefer correctness, clarity, testability, security, and simplicity over speculative extensibility or unmeasured performance.

## Container and cloud rules

- Use Make and Podman for build, test, demo, packaging, and cloud tooling.
- Do not install Python packages, AWS CLI, AgentCore CLI, Node.js, or CDK on the host.
- Default CI must not receive AWS credentials, deploy resources, or call a paid model.
- Mount the local AWS configuration read-only only for explicit cloud commands.
- Cloud commands must print the target account in redacted form, region, stack names, and model before acting.
- Deployment and teardown must target exact project stack names and artifact prefixes.
- Keep the model, turn count, tool-call count, timeout, and verification corpus bounded.
- Never substitute fixture output after a live model or service failure.

## Quality gate

Before completing a change:

1. Run the smallest relevant test.
2. Run the full account-free suite.
3. Run `make test-container` before opening or merging a pull request.
4. Exercise relevant failure and denial paths.
5. Confirm logs and evidence contain no secrets or unnecessary payloads.
6. Review the diff for scope, dead code, unrelated formatting, and speculative abstractions.
7. Keep README commands truthful and label fixture and AWS evidence separately.

Every non-trivial policy, approval, state transition, retry, reconciliation, and trust-boundary rule needs a runnable test that fails when the rule is removed.

## Change workflow

```text
Issue with acceptance criteria
-> focused branch from current main
-> smallest complete change
-> independent diff review
-> Podman checks
-> pull request
-> human approval
-> squash merge
-> branch deletion
```

Do not push implementation directly to `main` or merge autonomously. Add an ADR only for a lasting choice with real alternatives and consequences.

## Definition of done

The MVP is done when the frozen offline suite passes, bounded AgentCore verification proves the governed tool and policy path, exact approval cannot be modified or replayed, post-commit response loss reconciles to one effect, evidence is sanitized, teardown is proven, and all public commands work from a clean checkout through Podman.
