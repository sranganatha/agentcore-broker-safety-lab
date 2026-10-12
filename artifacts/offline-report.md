# Offline broker safety demonstration

This report uses deterministic fixtures. It does not claim live model or AWS execution.

- Execution mode: FIXTURE
- Model: deterministic-fixture
- Network required: false
- Cloud calls: 0
- Policy cases: 15
- Result: PASS

| Case | Result |
|---|---|
| offline_policy_corpus_matches | PASS |
| fixture_model_proposes_allowed_action | PASS |
| fixture_model_does_not_write | PASS |
| unapproved_action_requires_approval | PASS |
| operator_approval_is_exact | PASS |
| changed_arguments_are_denied | PASS |
| approved_action_commits_once | PASS |
| replay_returns_same_receipt | PASS |
| consumed_approval_is_denied | PASS |
| lost_response_is_uncertain | PASS |
| reconciliation_returns_one_effect | PASS |
| retry_after_reconciliation_returns_same_receipt | PASS |
