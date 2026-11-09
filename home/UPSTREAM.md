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
