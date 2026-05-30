# Dry-run artifact cleanup guidance

## Scope

This document explains how operators may identify and remove dry-run artifacts after controlled generated-strategy research tests.

It is documentation only. It does not add automated cleanup authority.

## Phase 16 dry-run identifier

The Phase 16 dry-run strategy ID was:

`DRYRUN_PHASE16_20260513T143641Z`

Version:

`v1`

Hypothesis ID:

`HYP_GEN_FBR_RANGING_000`

Dataset ID:

`DATA_XAUUSD_H4_0DB89C617104`

## Cleanup principles

- Prefer retaining evidence artifacts until the dry-run report has been reviewed.
- Never remove production strategies as part of dry-run cleanup.
- Never remove production specs as part of dry-run cleanup.
- Never modify lifecycle records as part of dry-run cleanup.
- Never remove approval records unless the operator has a separate explicit retention policy.
- Never use broad globs that may cross from generated paths into production paths.

## Paths that may contain dry-run artifacts

Review these locations manually for strategy-specific dry-run artifacts:

- `automated/generated_strategies/<strategy_id>/<version>/`
- `automated/generated_specs/`
- research evidence/artifact directories used by the registry
- generated baseline result artifacts
- generated robustness result artifacts
- generated final holdout result artifacts
- queue specs created specifically for the dry run

## Paths that must not be touched by dry-run cleanup

- `automated/strategies/`
- production strategy specs
- registry schema/migration files
- lifecycle transition records
- live trading configuration
- shared runner implementation files

## Safe manual cleanup checklist

Before deleting anything, confirm:

- The path includes the exact dry-run strategy ID.
- The path is under a generated or dry-run-specific location.
- The path is not under `automated/strategies/`.
- The path is not a shared runner file.
- The artifact is not required by a retained review packet.
- The dry-run report has already been saved.

## If a cleanup command is added later

Any future cleanup command must:

- default to dry-run/preview mode
- require an explicit strategy ID
- refuse empty strategy IDs
- refuse broad glob patterns
- print planned deletions before deleting
- refuse to delete anything under `automated/strategies/`
- refuse to delete registry schema/migration files
- refuse to delete lifecycle records
- have behavioral safety tests
