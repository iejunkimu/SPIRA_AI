# Contributing to SPIRA

How the team works in this repository. Keep it short; if a rule here stops being useful, change it in a PR.

## Branches

| Branch | What it holds | Who writes |
| --- | --- | --- |
| `main` | Submitted / presented state only | PR from `develop` on release days |
| `develop` | Integrated work in progress (default branch) | PR from work branches |
| `<part>/<topic>` | One small piece of work | Anyone, for their own part |

- Name work branches `<part>/<topic>`, e.g. `server/cases-migration`, `hub/pull-ack`, `app/share-flow`, `simulator/viewer`, `contracts/enums`, `docs/architecture`. Use `repo/<topic>` for repository-wide work.
- Keep a branch small and short-lived: open a PR within 1–3 days.
- Do not push directly to `main` or `develop`. Both are protected.

## Pull requests

1. Branch from the latest `develop`.
2. Open a PR into `develop`. The PR template fills in **What / Why / Checked**.
3. Reviewers are assigned by `CODEOWNERS`.
   - Into `develop`: approval is not required by GitHub, but get one from the affected owner when the PR touches another part's folder or `contracts/`.
   - Into `main`: one approval is required.
4. CI must pass.
5. **Squash merge**, then the branch is deleted automatically.

Draft PRs are welcome for work that is not ready yet.

## Folders and owners

| Folder | Owner |
| --- | --- |
| `server/` | Seojin |
| `hub/` · `app/` · `simulator/` | Yejoon |
| `contracts/` | Seojin (draft) + affected owners review |
| `docs/paper/` | Jeewon |
| `docs/` (other) | Shared |
| Knowledge base | separate repository [SPIRA_KB](https://github.com/CSE-SPIRA/SPIRA_KB) (Uijin) |

Change another part's folder only with that owner as a reviewer.

Every part folder has, at its top level:
- `README.md`: what the part is and how to run it
- `spec.md`: the part's specification (English)
- `.env.example`: environment variable names, if the part uses any

Everything else inside a part folder is decided by its owner.

## Contracts (`contracts/`)

`contracts/` holds the values parts must agree on (API, JSON schemas, enums, fixtures).

1. Update the Notion **SPIRA integration contract** first.
2. Open a PR that changes `contracts/` only. Do not mix it with code changes.
3. Every affected owner reviews it.

Part folders may keep internal schemas, but if they differ from `contracts/`, `contracts/` wins.

## Undoing a change

- Use **Revert** on the merged PR (or `git revert <sha>`), then merge the revert PR.
- Never rewrite shared history (`git reset`, force-push) on `main` or `develop`.
- A deleted branch can be restored from its PR page.

## Language

Everything on `main` (what we submit) is in English.

| What | Language |
| --- | --- |
| Code, comments, commit messages, `contracts/` | English |
| `<part>/spec.md` | English. Must be up to date on release days |
| `<part>/spec.ko.md` | **Optional.** Keep a Korean working copy only if it helps you. If you do, translate it into `spec.md` on release days and write `Translated from spec.ko.md @<commit>` on the first line of `spec.md` |

## Releases (`develop` → `main`)

Only on submission and presentation days (e.g. 11/27, 12/4, 12/11, 12/14).

1. Update the English `spec.md` files.
2. Open a PR from `develop` to `main` (What: "Release for 12/4 demo").
3. After merge, create a tag (`v-1204`) and a GitHub Release with **Generate release notes**.
4. Add one or two lines of `Known limitations` at the end of the release notes.

## Secrets

- Never commit connection URLs, passwords, or API keys. Put them in your local `.env`.
- `.env.example` lists variable names only.

## Big changes

Deleting or transferring a repository, changing branch protection, or changing these rules: say it in the team chat first.
