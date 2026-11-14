# Contracts

Values every part must agree on. **This folder is the single source of truth between parts**; if a schema inside a part folder differs, this folder wins.

Source: the Notion page *SPIRA integration contract* (agreed items only). Change Notion first, then open a `contracts/` only PR (see `.github/CONTRIBUTING.md`).

| Path | What |
| --- | --- |
| `openapi/` | `app.yaml` (App ↔ Server), `hub.yaml` (Home Hub ↔ Server) |
| `schemas/` | JSON Schemas: plan, devices, event, execution result, card, reaction, … |
| `enums.json` | Shared string values (events, triggers, statuses, device types, …) |
| `state-mapping.md` | State mapping table (C-7) |
| `rules.md` | Common rules: IDs, time, versions, polling, demo clock |
| `kb/` | Knowledge base lists (intents, calendar tags, household types) and intent ID mapping |
| `fixtures/` | Example data per scenario, starting with `ep_9031` |
| `check/` | Script that validates fixtures against schemas (run in CI) |

Connection URLs and keys are never stored here, only environment variable names.

_Contents are filled in step 4-⑤._
