# Team-facing write and promotion contract

SPIRA_AI holds publication/submission artifacts, shared integration state and explicitly promoted runnable milestones. It is not the ordinary personal development repository. `main` is a high-friction team-facing branch: a commit here means “this change is deliberately being presented to the team.” GC is not permission to rewrite team product artifacts.

The intended flow is research/candidate in lg_project_past → explicit implementation choice → implementation/experiment/validation in settle_home → deliberate milestone promotion → SPIRA_AI. Keep each native owner authoritative. Do not automatically synchronize Home or merge every experiment.

For Home promotion, create a dedicated branch and PR, and include a small receipt with all six fields:

- **SOURCE:** settle_home SHA and branch.
- **PURPOSE:** why this state is being exposed to teammates.
- **SCOPE:** exact files/contracts/runtime pieces included.
- **VALIDATED:** tests, fixtures, runtime and device evidence actually checked, with identities.
- **NOT PROMOTED:** experiments/unfinished work deliberately excluded.
- **OPEN / LIMITS:** concrete remaining gaps; historical OPEN labels are not current team decisions.

Resolve the source SHA, compare it to the preceding milestone, validate the promoted scope and inspect the PR diff. Merge to the intended team integration/publication branch only as an explicit integration act. Source validation does not itself establish team adoption, and a team milestone does not automatically become main/publication.

Keep `home-runtime-team-integration-2026-11-09` as a stable record of that milestone, including its existing validation/clarification history. Do not append new development there indefinitely. A materially new state gets a new branch or explicitly versioned successor and its own receipt. No old milestone/runtime was mutated in this correction.

Current team product decisions are settled. Flutter mobile for iOS and Android is the App direction; exact builds/native integration tests are implementation evidence. Keep historical OPEN records in history.
