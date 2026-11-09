# Galaxy S10 team staging

The Galaxy S10 5G runs the team stack under `/opt/spira-team-home`. It uses the phone's existing Docker Engine socket at `/opt/spira-home/docker.sock` and its Android-compatible host networking. The Compose project is `spira-team-home`; its image is tagged with the team commit, configuration comes from an archived release, and every Home, world and fixture gets a separate state directory.

| Profile | Home | Independent world | Service fixture | MQTT topic prefix |
| --- | --- | --- | --- | --- |
| Base, wall clock | 8750 | 8751 | — | `spira/team/base` |
| `v32`, virtual clock | 8752 | 8753 | 8754 | `spira/team/v32` |
| `reality`, virtual clock with world fault controls | 8755 | 8756 | — | `spira/team/reality` |

All listeners bind to loopback. The team broker listens on 19883. Compose mounts only team state and the team broker config. The project has no dependency on a development-stack service, DB, or Matter fabric.

## Deploy a team commit

On the workstation, check that the selected ports are free for a first install and that the device and daemon are the intended ones:

```sh
ssh s10plus 'ss -lnt; docker -H unix:///opt/spira-home/docker.sock ps'
revision=$(git rev-parse HEAD)
git archive "${revision}:home" | gzip -n > "/tmp/spira-team-$revision.tar.gz"
shasum -a 256 "/tmp/spira-team-$revision.tar.gz"
ssh s10plus "umask 077; mkdir -p /opt/spira-team-home/releases; mkdir /opt/spira-team-home/releases/$revision"
scp "/tmp/spira-team-$revision.tar.gz" "s10plus:/opt/spira-team-home/releases/$revision.tar.gz"
ssh s10plus "sha256sum /opt/spira-team-home/releases/$revision.tar.gz"
```

Confirm both archive hashes match. On the phone, paste the same commit SHA when prompted:

```sh
root=/opt/spira-team-home
printf 'Team commit SHA: '
read -r SPIRA_TEAM_REVISION
export SPIRA_TEAM_REVISION
release="$root/releases/$SPIRA_TEAM_REVISION"
daemon=unix:///opt/spira-home/docker.sock
tar -xzf "$release.tar.gz" -C "$release"
mkdir -p "$root/state" "$root/evidence"
docker -H "$daemon" build --network host \
  --label org.opencontainers.image.revision="$SPIRA_TEAM_REVISION" \
  -t "spira-team-home:$SPIRA_TEAM_REVISION" "$release"
docker -H "$daemon" compose -f "$release/deploy/compose.team-s10.yaml" \
  --profile v32 --profile reality up -d --no-build
curl -fsS http://127.0.0.1:8750/health
curl -fsS http://127.0.0.1:8752/health
```

Wait for both health endpoints. Repeat with the same project name for a new release; keep the state directory. To stop the team project while keeping its state, use the same Compose arguments with `stop` in place of `up -d --no-build`.

## Run validation on the phone

Use a separate test environment with `requirements.lock`, `pytest`, `pytest-asyncio`, and the optional `thinqconnect==1.0.14` SDK installed. Run from the archived release directory with `SPIRA_TEAM_REVISION` set as above, `PYTHONPATH="$PWD/src"`, and `SPIRA_TEST_MQTT_PORT=19883`. Run scenarios serially within a profile. The examples use `python` from the test environment.

```sh
python -m pytest -q
python scripts/trace_household.py --home http://127.0.0.1:8750 --world http://127.0.0.1:8751 \
  --output "$root/evidence/base.json"
mkdir -p "$root/evidence/v32"
for scenario in birthday care birthday-public travel; do
  python scripts/run_v32.py "scenarios/$scenario.json" \
    --home http://127.0.0.1:8752 --world http://127.0.0.1:8753 \
    --service http://127.0.0.1:8754 --output "$root/evidence/v32/$scenario.json"
done
python scripts/trace_v32_adversaries.py --home http://127.0.0.1:8752 \
  --world http://127.0.0.1:8753 --service http://127.0.0.1:8754 \
  --restart-home --output "$root/evidence/v32/adversaries.json"
python scripts/trace_reality_adversary.py --s10 --runs 12 --seed 2000 \
  --output "$root/evidence/reality"
```

The restart option targets only the named team v3.2 Home container on the phone. Reality process faults are limited to the team project's allowlisted targets. Keep Matter lab helpers off the development stack's network and fabric; the team MQTT Compose project does not start Matter or Home Assistant.

For the virtual Matter plug check, install the archived lockfile into the team's test directory using Node.js 22+ and npm. The check also uses the same Python test environment at `$root/validation/venv` and requires Linux `unshare` and `ip`. Copy [trace_team_s10_matter.sh](../scripts/trace_team_s10_matter.sh) from the validation commit to the phone if it is newer than the staged release.

```sh
deps="$root/validation/matter"
mkdir -p "$deps"
cp "$release"/tools/matter/package*.json "$deps/"
npm ci --prefix "$deps" --ignore-scripts
cp "$release"/tools/matter/*.mjs "$deps/"
node --test "$deps/mapping.test.mjs"
sh /path/to/trace_team_s10_matter.sh
```

The protocol trace creates a private network namespace, commissions a standard virtual plug with a fresh fabric, and checks independent readback and override through Home's public API. It stops the test processes and keeps their receipts under the team root.
