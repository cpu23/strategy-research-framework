# Agent Workspace Protocol

## Purpose

The agent workspace lets external agents hand structured research artifacts to the Research OS through a safe inbox/outbox bundle protocol. It is a file adapter only. It validates, imports safe research specs, quarantines rejected bundles, and writes validation reports.

It does not run experiments, add queue execution, approve baselines, approve final holdouts, apply lifecycle transitions, or create production/live behavior.

## Directories

```text
automated/agent_workspace/inbox/
automated/agent_workspace/working/
automated/agent_workspace/outbox/
automated/agent_workspace/rejected/
automated/agent_workspace/logs/
```

Incoming bundles live under `inbox/`. Rejected bundles are copied into `rejected/` with a validation report. Validation logs may be written under `logs/`.

## Bundle Shape

Each bundle is a directory containing `manifest.yaml`:

```yaml
schema_version: agent_workspace_bundle_v1
bundle_id: AWB_RESEARCH_LIB_001
agent_role: research_librarian
allowed_recommendations: [reject, revise_thesis, request_manual_review, defer]
artifacts:
  - artifact_type: research_source_record
    path: artifacts/source.yaml
    target_name: SRC_CASE_VOL_BREAKOUT_001.yaml
  - artifact_type: edge_thesis
    path: artifacts/edge.yaml
    target_name: EDGE_VOL_COMPRESSION_BREAKOUT_001.yaml
```

Artifact paths must be relative to the bundle and must not escape the bundle root.

## Role-To-Artifact Permissions

The adapter enforces a conservative role map:

- `research_librarian`: research source records, edge theses, extraction reports.
- `experiment_designer`: mutation recipes, generated hypothesis batches, screening reports, campaign plans, and campaign planning reports.
- `statistical_reviewer`, `portfolio_reviewer`, `red_team_reviewer`: similarity/diversity and campaign analysis reports.

Unknown roles and mismatched artifact types are rejected.

## Canonical Imports

Accepted importable artifacts are copied only into canonical spec folders:

- `research_source_record` -> `automated/specs/research_sources/`
- `edge_thesis` -> `automated/specs/edge_theses/`
- `mutation_recipe` -> `automated/specs/mutation_recipes/`
- `generated_research_campaign_plan` -> `automated/specs/research_campaigns/`

The adapter rejects path traversal, absolute artifact paths, directory-bearing target names, `.mq5` artifacts, and any destination under `automated/strategies/`.

## Validation Reports

Validation reports use:

```yaml
schema_version: agent_workspace_validation_report_v1
artifact_type: agent_workspace_validation_report
status: accepted | rejected
accepted_artifacts: []
rejected_artifacts: []
errors: []
warnings: []
authority:
  workspace_protocol_only: true
  execution_authority: false
  queue_execution_authority: false
  baseline_decision_authority: false
  final_holdout_decision_authority: false
  state_transition_authority: false
  production_authority: false
  live_trading_authority: false
```

Reports intentionally carry no approval fields and no lifecycle proposals.

## Rejection Rules

Bundles are rejected for:

- missing or invalid manifest schema
- unknown role
- artifact type outside role permissions
- unregistered artifact type
- path traversal or absolute artifact paths
- missing artifact files
- `.mq5` artifact files
- writes or copies into `automated/strategies/`
- approval, lifecycle, production, or live-trading authority fields
- forbidden values in `allowed_recommendations`

Forbidden values remain forbidden as allowed recommendations:

```text
promote_to_production
production_candidate
live_trading_candidate
```

## Non-Goals

- No new execution authority.
- No new queue execution authority.
- No lifecycle apply.
- No baseline approval.
- No final-holdout approval.
- No production behavior.
- No live trading behavior.
