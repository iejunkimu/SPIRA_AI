# SPIRA

**Proactively Preparing Smart Homes via Retrieval-Augmented Purchase Intent Inference over Synthetic Knowledge**

Hanyang University, Fall 2026<br>
`CSE406 Software Engineering` · `ITE351 AI & Applications`

## Re-entry and repository standing

`main` publishes the Submission A+B paper. Its last paper publication is `21f8a90` (2026-11-06); this is a submission artifact, separate from ongoing native development and its implementation evidence.

- [Team Home milestone](https://github.com/iejunkimu/SPIRA_AI/tree/home-runtime-team-integration-2026-11-09/home): a separately validated integration snapshot on its named branch. Its [UPSTREAM record](https://github.com/iejunkimu/SPIRA_AI/blob/home-runtime-team-integration-2026-11-09/home/UPSTREAM.md) identifies the selected source and promotion process. There is no automatic synchronization or blanket adoption of newer experiments.
- [Independent Home development](https://github.com/iejunkimu/settle_home): runtime, connector and experiment owner; access is restricted to that repository's collaborators.
- [SPIRA_KB](https://github.com/uijinlee-luj/SPIRA_KB): current synthetic-case pipeline and shared embedding package, maintained separately by its native owner.

**TEAM-FACING write contract:** a commit here means this state is deliberately presented to the team. Personal development stays in the native owners. See [TEAM-WRITE-CONTRACT.md](TEAM-WRITE-CONTRACT.md) and the promotion receipt template before proposing a new milestone. Current App product direction is Flutter mobile for iOS and Android; publication-version tables and old OPEN episodes do not reopen it.

## Published Submission A+B

This repository preserves the published CSE406 Submission A+B paper and its LaTeX source.

- [Submission PDF](document/main.pdf)
- [LaTeX source](document/main.tex)

The paper retains the submitted A requirements and adds the Submission B development environment and specifications in one IEEE document:

1. Introduction
2. Requirements
3. Development Environment
4. Specifications
5. References

## Project overview

SPIRA connects a user-shared order to bounded household preparation. It retrieves reviewed synthetic purchase-intent cases, interprets a possible household situation, validates proposed preparation with deterministic rules, notifies the user, and delegates eligible execution to the home-side runtime. Observed results and explicit user reactions are recorded separately from future execution permission.

```text
Shared Order and Context
    -> Synthetic Purchase-Intent Retrieval
    -> Intent or No-Intent Judgment
    -> Deterministic Eligibility and Plan Validation
    -> Notification and User Controls
    -> Home-Side Trigger, Execution, and Readback
    -> Observed Result and Household Memory
```

The representative scenario is a child's birthday party. The prototype distinguishes relevant party items from unrelated purchases, proposes only supported household tasks, and labels simulated and physical device paths explicitly.

## Responsibility boundaries

- **Knowledge Retrieval:** constructs, reviews, embeds, and retrieves synthetic purchase-intent cases.
- **SPIRA Service:** interprets intent, derives household needs, validates and versions plans, prepares explanations, and updates memory.
- **App:** supports order sharing, notification, explanation, `STOP ALL`, and per-task disable controls.
- **Home:** revalidates current capability and physical state, evaluates local triggers, dispatches commands, reads results, and handles override or restoration.
- **Simulator / Physical Devices:** expose capabilities, events, independently maintained state, and observable effects.

An LLM response never authorizes a physical action by itself. Product policy, capability constraints, current state, user intervention, and prohibited-action rules remain deterministic checks.

## Development baseline

The paper specifies the current prototype baseline: Python 3.12 for retrieval, Service, Home logic, and simulation; Flutter 3.47.5 for the App; PostgreSQL 16 with pgvector for persistent data and retrieval; and an existing Galaxy S10 5G as the Home prototype substrate. The development workstation and estimated incremental cost are recorded in Section III.

## Build

The repository includes `document/IEEEtran.cls`. From the `document` directory, compile with Tectonic:

```bash
tectonic -X compile main.tex
```

## Team

| Name | Responsibility |
| --- | --- |
| Jeewon Lee | Development management and submission integration |
| Uijin Lee | User evaluation and synthetic purchase-intent cases |
| Seojin Lee | Customer scope and requirement review |
| Yejoon Kim | Service, model integration, device adapters, and tests |
