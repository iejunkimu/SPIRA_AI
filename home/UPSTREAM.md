# Upstream and standing

| | |
| --- | --- |
| Source repository | [`iejunkimu/settle_home`](https://github.com/iejunkimu/settle_home) (private) |
| Source branch | `codex/reality-adversary-closure-2026-11-08` |
| Source commit | `4d9656fd9c9b897c65da47da008f2a1727a94947` |
| Source relationship | The remote candidate was two commits ahead of its `main` (`be9b42ee6394c36781781ec96a2d816001a0219d`), with no divergent commits. |

This directory is the SPIRA team repository's adopted Home implementation snapshot.
The independent `settle_home` repository remains the development and experimental authority.
Changes made here are team-integration/readability changes unless explicitly promoted back upstream.

The baseline import copies tracked blobs at the source commit for the runtime, connectors,
contract exports, independent testbed and adversary, tests, scenario fixtures, Matter helpers,
desktop Compose files, and their packaging. The upstream paper copy, historical evidence,
research notes, and development-node provisioning are outside the team snapshot. The team
specification is in [the SPIRA paper](../document/main.pdf).

## Promote a milestone

`settle_home` owns new experiments and frontier work. `SPIRA_AI/home` carries an explicitly selected, runnable milestone for team use and submission. There is no automatic synchronization.

1. Select an upstream source SHA and confirm its branch relation and scope.
2. Compare its runtime, contracts and validation inputs with the recorded source SHA; separate experiments from the intended milestone.
3. Import the selected changes on a team integration branch, preserving team documentation and staging isolation.
4. Rerun the upstream and team regressions, including the Service fixture and regenerated schema comparison.
5. Deploy the new team commit as a separate S10 release and verify runtime behavior and preserved state.
6. Update the source SHA here and record the deployed team commit, tests, and open gaps in the validation record.
