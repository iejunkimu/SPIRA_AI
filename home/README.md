# SPIRA Home

This is the runnable Home implementation adopted into the SPIRA team repository. It implements the Home side of the [Settle 3.2 specification](../document/main.pdf). [UPSTREAM.md](UPSTREAM.md) identifies the exact source and who owns future changes.

| Program | Responsibility |
| --- | --- |
| SPIRA Home Runtime | Accepts a versioned Service plan, checks present household eligibility, waits for local triggers, and dispatches eligible work. It verifies effects by reading independent state, reconciles overrides and ownership, restores only what it still owns, and records results. |
| Service Bridge | Pulls or accepts C-1 plans; sends C-2 device/capability inventory, C-3 household events, and C-4 execution and restore results. Home does not implement inference, RAG, or household memory. |
| MQTT connector | Connects to a programmable, independent household and fault substrate. It makes delayed, rejected, stale, or unobservable effects reproducible. |
| Matter connector | Uses a standard Matter controller/device path for commissioning, subscriptions, commands, and network readback. The supplied devices are virtual protocol peers. |
| Home Assistant connector | Integrates with an existing smart home platform through its HTTP and WebSocket surfaces. |
| ThinQ connector | Defines the official vendor SDK/profile boundary, including the capabilities and evidence the adapter can actually claim. |
| Household Testbed | Owns devices, command handling, events, clock, and SQLite state in a separate process. It can apply, change, or withhold an effect independently of what Home requested. |
| Reality Adversary | Exercises Home's invariants through public APIs and independent world, protocol, and process faults. It seeks counterexamples, such as a stale read being accepted as proof of an effect. |
| S10 deployment | Runs the team snapshot on an actual Galaxy S10 5G. The [staging stack](docs/s10-staging.md) has its own Compose project, data, ports, and MQTT topics. |

Read the [architecture and code map](docs/architecture.md) to follow a plan through the code. The [validation record](docs/team-integration-validation.md) gives the measured scope of this adoption.

## Run locally

From `home/`, with Python 3.12+, Docker Compose, and Node.js 22+ for the Matter helper checks:

```sh
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.lock -e '.[test,thinq]'
mkdir -p .local
docker compose up -d --build
curl -fsS http://127.0.0.1:8710/health
.venv/bin/python -m pytest -q
.venv/bin/python scripts/trace_household.py --output .local/household.json
(cd tools/matter && npm ci && node --test mapping.test.mjs)
```

The base stack has a wall clock. Home is on port 8710, the independent world is on 8711, and MQTT is on 18883. All published ports bind to loopback. The process tests need a reachable broker on 18883 (or `SPIRA_TEST_MQTT_PORT`); the full run should have no MQTT skips. Use a disposable project for traces that mutate the virtual world.

For the Settle 3.2 fixture, bring up the separate virtual clock, world, and Service fixture on ports 8730–8732. Run scenarios serially against this clock. The fixture supplies prewritten plans and receives contract messages.

```sh
docker compose -f compose.v32.yaml up -d --build
for scenario in birthday care birthday-public travel; do
  .venv/bin/python scripts/run_v32.py "scenarios/$scenario.json" --output ".local/$scenario.json"
done
.venv/bin/python scripts/trace_v32_adversaries.py --output .local/v32-adversaries.json
.venv/bin/python scripts/trace_reality_adversary.py --local --runs 12 --seed 2000 --output .local/reality
```

The virtual operator console is at `http://127.0.0.1:8730/console`. The OpenAPI document is at `/docs` and the tracked exports are in [contracts/](contracts/).

## Where things live

```text
home/
├── contracts/                 HTTP/OpenAPI and shared schemas
├── src/spira_home/            API, bootstrap, runtime, bridge, store and rules
│   ├── connectors/            MQTT, Matter, Home Assistant and ThinQ
│   └── testbed/               Independent world and Service contract fixture
├── src/reality_adversary/     Scenario generation, fault runner and invariant checks
├── scenarios/                 Settle 3.2 and adversarial replay inputs
├── tools/matter/              Standard controller and virtual device helpers
├── deploy/                    Team Galaxy S10 Compose and broker configuration
├── scripts/                   Contract export and executable traces
├── tests/                     Python contract, runtime and subprocess tests
└── docs/                      Code map, staging and validation
```
