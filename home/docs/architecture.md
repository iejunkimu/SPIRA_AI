# Home code map

Start at [the application](../src/spira_home/app.py), then follow `POST /plans` through [the wire translator](../src/spira_home/bridge.py) into [the runtime](../src/spira_home/runtime.py). `spira_home.app:app` remains the Uvicorn entry point. `Runtime` and `ServiceBridge` keep their original import paths. The testbed stays inside the Python package because Compose starts the world and Service fixture as separate Uvicorn applications from the same built image.

| Boundary | Read first | Why it exists |
| --- | --- | --- |
| Startup and background work | `bootstrap.lifespan`, `worker`, `consume`, `publish` | Startup recovers SQLite before scheduling work, subscribes to configured connectors, and starts Service delivery. A late stop after dispatch still needs reconciliation; it cannot erase an actual outcome. `publish` is the older optional result webhook. |
| Plan admission and version fencing | `bridge.translate`, `Runtime.admit_plan`, `submit`, `gate` | Validate translated actions before committing the whole plan version. Persist a per-plan version floor so an old plan waking up after I/O cannot start a new effect. The same identity and body are idempotent; changed content under the same identity conflicts. |
| Observation intake | `Runtime.observe`, `event_cursor`, `facts` | Deduplicate independent events and keep their durable order for triggers and C-3 delivery. An event cursor excludes pre-admission facts even when the clock timestamps are equal. A gap resets observed stability. |
| Trigger evaluation | `Runtime.local_trigger`, `condition`, `tick`, [rules.py](../src/spira_home/rules.py) | Correlate local facts such as doorbell and contact, or sample a condition over time. Service's interpretation cannot establish whether a physical trigger has happened at Home. |
| Execution gate and precheck | `Runtime.gate`, `precheck`, early `execute` | Check validity/deadline, controls, current capability, room, quiet hours, and current state. Recheck authority after awaited reads so a late stop or supersession still fences new dispatch. |
| Dispatch and verification | `Runtime.execute`, `failure`, `result` | Reserve a durable operation before handing off the command. A connector receipt tells whether it accepted the command; fresh readback must independently contain the desired effect before Home reports it verified. Retries are bounded and need evidence that retrying is safe. |
| Override and ownership | `Runtime.reconcile` and its verification-time counterpart in `execute` | Give manual/external changes priority, including when they arrive before ownership can be recorded. Compare property values and available revisions. A changed property can lose ownership without losing the record of the original result. An unknown actor remains `UNKNOWN`. |
| End, job stop, and restore | `Runtime.lifecycle`, `end`, `stop_job`, `_restore` | Match the admitted end context and the original job identity. Restore only still-owned properties with a fresh read and a native revision guard, so a newer change is not overwritten during restore. |
| Durable state and restart | `Runtime.__init__`, [store.py](../src/spira_home/store.py) | Keep task state, floors, operation identities, ownership, and delivery obligations in local SQLite. An interrupted dispatch becomes indeterminate rather than being sent again. A pre-crash timer cannot prove stability across downtime. |
| Independent world and adversary | [world.py](../src/spira_home/testbed/world.py), [runner.py](../src/reality_adversary/runner.py), [checker.py](../src/reality_adversary/checker.py) | Keep physical truth and test perturbations outside Home to avoid self-confirming verification. The oracle distinguishes a pass, a violation, and insufficient evidence. |

## HTTP and Service boundaries

The Home integration API is `/health`, `/capabilities`, `POST /plans`, and the versioned task, result, event, plan summary and control endpoints in `app.py`. `/v1/timeline`, `/v1/states`, and `/console` are operator diagnostics. The `/v1/test/*` writes require a virtual Home clock, and clock/world forwarding also requires the independent `WORLD_URL`. These are existing route and access rules; the grouping is for readers. The staging listeners are loopback-only.

[service.py](../src/spira_home/service.py) owns HTTP polling and outbox delivery. [bridge.py](../src/spira_home/bridge.py) validates and translates the Service wire model. The [Service fixture](../src/spira_home/testbed/service_fixture.py) is an independent test peer for these endpoints:

| Settle 3.2 | Direction | Actual path and implementation |
| --- | --- | --- |
| C-1 plan | Service → Home | Push `POST /plans` or pull `GET /v1/hub/plans`; `translate` and `Runtime.admit_plan` converge on the same admission path. Pull requests current snapshots (`since_version=0`) because a plan version is per plan, not a global cursor. |
| C-2 inventory | Home → Service | `PUT /v1/hub/households/{household}/devices` from `ServiceBridge.register`; capabilities come from connector discovery. |
| C-3 household event | Home → Service | `POST /v1/hub/events`; `event_wire` formats raw and derived facts from the event outbox. |
| C-4 execution/result | Home → Service | `POST /v1/hub/executions`; `execution_wire` reports status, command and effect evidence separately. A persistent hub ID and outbox sequence stabilize delivery identity after restart. |

The [plan schema](../contracts/plan.schema.json) preserves the Service plan ID, version, task/action identity, targets and effects, triggers, validity and prechecks. The normalized [task schema](../contracts/task.schema.json) and results also carry `episode_id`, distinct from `plan_id` and `task_id`. A newer version or a stop fences **new** dispatch; an already handed-off command can still take effect without native cancellation. Continuous physical stability between samples, restoration without native conditional writes, and RVC job attribution without a per-run identity are outside the available evidence.
