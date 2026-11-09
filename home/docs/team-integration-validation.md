# Team Home integration validation

Validated on 2026-11-09. This is a new run of the selected source and team snapshot, not a restatement of upstream evidence.

| Identity | Value |
| --- | --- |
| Source | `iejunkimu/settle_home` · `codex/reality-adversary-closure-2026-11-08` |
| Source commit | `4d9656fd9c9b897c65da47da008f2a1727a94947` |
| Source branch relation | Remote and local candidate heads matched; candidate was two commits ahead of source `main` at `be9b42ee6394c36781781ec96a2d816001a0219d`, with zero divergence. |
| Team repository | `iejunkimu/SPIRA_AI` · `codex/home-runtime-team-integration-2026-11-09`, based on team `main` at `21f8a90b95ae58d91262372eb433b66cd2bf553e` |
| Exact deployed team commit | `c029e95f26e787db3188884f4102b2a5abdcd442`. Later commits add documentation and validation tooling/evidence; runtime and Compose are identical. |
| Deployed Git subtree | `ac70cf88bd647e4312a2d8dfafc4b33c2382dbf2` (`home/`) |
| Release archive SHA-256 | `9fe574e10498f0ec3b4f53dbb865c6c56ce898bb015b79b2d84cdde6275bc7e1` (matched before and after transfer) |
| Running image ID | `sha256:09de61f75baafd3400262591107a9dfa365a7f17f94095de7a7948b894b8da65` |

The baseline archive imported 110 tracked upstream files with identical Git blob IDs and modes. The source checkout and remote refs were unchanged. The team snapshot moves startup, shutdown, and workers to `bootstrap.py`; groups integration, operator, and guarded test HTTP routes; and documents runtime and Service Bridge responsibilities. Function definitions matched the baseline after removing documentation strings. Team deployment and process-test tooling use only the team project's ports, broker, state, and allowlisted containers.

**Explicit semantic changes: NONE.** The shared wire contract, task/result behavior, and execution safety rules are unchanged. Regenerating the six model schemas and OpenAPI for both snapshots produced equal JSON structures matching all seven tracked exports.

## Regressions

| Check | Source on workstation | Team on workstation | Team on Galaxy S10 5G |
| --- | --- | --- | --- |
| Python (including real MQTT process tests) | 102 passed | 102 passed | 102 passed |
| Matter JavaScript mapping and persistent journal | 4 passed | 4 passed | 4 passed |
| Base MQTT T0–T6 deduplication and fault traces | 11 passed | 11 passed | 11 passed |
| v3.2 birthday, care, public birthday, travel | 16 / 9 / 16 / 5 assertions passed | 16 / 9 / 16 / 5 passed | 16 / 9 / 16 / 5 passed |
| v3.2 adversaries | 13 passed | 13 passed | 13 passed, including actual Home restart |
| Reality Adversary against independent MQTT processes | 48 passed | 48 passed | 48 passed against team Compose services |
| Shared schema / OpenAPI | 7 regenerated and matched | 7 regenerated and matched | Live OpenAPI matched on all three Home listeners |

The Reality Adversary runs used 36 named cases and 12 generated seeds (2000–2011), with zero scenario-level violations or inconclusive outcomes in each run. Individual invariants can be unchallenged by a given trace. The base T6 trace verifies deduplication; the restart proof comes from the v3.2 outage trace and Reality Adversary process faults.

## Galaxy S10 acceptance

The SM-G977B / ARM64 phone ran the team release from `/opt/spira-team-home/releases/c029e95f26e787db3188884f4102b2a5abdcd442`. Docker Compose project `spira-team-home` uses the phone's existing Engine and its own loopback ports (8750–8756, MQTT 19883), topic prefixes (`spira/team/*`), and state directory. The running container's 27 Python/console source files matched the release, and the image revision label matched the team commit.

- Home returned HTTP 200 on the wall clock, v3.2 clock, and Reality Adversary profiles. Independent MQTT world communication, plan admission, command acceptance and independent readback passed. The base suite also observed accepted commands with missing or mismatched effects without reporting verification.
- Service fixture round trip: C-1 pulled a queued plan and recognized a duplicate push; C-2 registered a nonempty capability inventory; C-3 delivered a raw doorbell event with unknown actor and a derived guest-arrival event tied to the plan; C-4 delivered verified results and replayed pending results after an outage. This peer is a contract fixture.
- During an injected Service outage, the team v3.2 Home container restarted, recovered its durable override, and published both the verified and overridden outcomes after Service recovered. Reality traces also replaced Home/world processes across uncertain work. All seven team SQLite databases passed read-only `PRAGMA quick_check`.
- The existing `/opt/spira-home` stack remained running. All 11 development container IDs, process IDs, and start times matched the preflight. The development Compose file, DB files and Matter fabric directories retained their identities. Existing Home, v3.2, Reality, and Matter endpoints responded after testing.

## Connector standing and limits

- **MQTT:** revalidated on the actual phone with an independent programmable virtual world, SQLite state, restart and fault pressure.
- **Matter:** on the phone, the archived helper code and team Home used a private Linux network namespace and fresh fabric storage. A standard virtual plug commissioned and subscribed over Matter; Home obtained `ACCEPTED` and fresh network readback as `VERIFIED`. An independent device-side toggle caused `OVERRIDDEN`, actor provenance remained `UNKNOWN`, and ending the plan preserved the external value. The checked-in lab script reproduced this result; it stopped the lab processes after testing. A fresh two-node lab did not reach a usable inventory under the helper's parallel commissioning; the adoption proof is the single virtual plug. No physical Matter device, Thread/BLE path, or multi-node team proof is claimed.
- **Home Assistant:** connector retained from upstream; no live Home Assistant rerun is claimed for this team validation.
- **ThinQ:** official SDK/profile validation only. No live LG appliance, vendor credentials, or physical effect was tested.
- The production Service and App were outside this test. A command already handed off may still take effect after stop/supersession; native cancellation/fencing would be needed to prevent it. Missing observations cannot prove continuous physical stability, missing native revision guards prevent automatic restore, and Matter RVC without per-run identity remains unverified. The phone was not rebooted.

Raw team validation output stays on S10 under `/opt/spira-team-home/evidence/`. Selected receipt digests:

| S10 receipt | SHA-256 |
| --- | --- |
| `base.json` | `647e3ff1370a5a56f4bdc8cef5788103f10028c1b4bb66963865c75b710b5f45` |
| `v32/adversaries.json` | `15c207199833e99703066f56dea31d08e9c5488d6a28dcbb82967d0fb117d905` |
| `reality/run-summary.json` | `19eb11764543d909153ba397c882a82f41d57a814aa82bc6e2d437e4da94cb6b` |
| `installed-source.txt` | `f9c1be6c2d96b44434ec0e84680c042ccb35f2c2928f22f0da74294082138a60` |
| `matter-plug/result.json` | `1fd9a016f35b7e6b6eba68f88b0807443b915e4b2be29623e930ea9087b1363f` |
| `pytest-full.log` | `b42e9c029a6e8dda036c966a172d51939e894c5006ef8209d0bc8c516aeee79f` |

Use the [staging guide](s10-staging.md) to run the team release again. Use the [explicit milestone promotion process](../UPSTREAM.md#promote-a-milestone) for future upstream changes; the two repositories do not sync automatically.
