#!/bin/sh
# Run on S10. An isolated network namespace keeps the lab off the existing fabric.
set -eu
root=${SPIRA_TEAM_ROOT:-/opt/spira-team-home}
: "${SPIRA_TEAM_REVISION:?Set SPIRA_TEAM_REVISION to the archived team commit}"
rel="$root/releases/$SPIRA_TEAM_REVISION"
deps="$root/validation/matter"
py="$root/validation/venv/bin/python"
cmp "$rel/tools/matter/package-lock.json" "$deps/package-lock.json"
test -d "$deps/node_modules"
lab=$(mktemp -d "$root/validation/matter-lab.XXXXXX")
e="$root/evidence/${lab##*/}"
m="$lab/tools"
mkdir -p "$e" "$lab/plug" "$lab/controller" "$m"
cp "$rel"/tools/matter/*.mjs "$m/"
ln -s "$deps/node_modules" "$m/node_modules"
printf 'Matter lab receipts: %s\n' "$e"
unshare --net -- /bin/sh -s -- "$rel" "$lab" "$e" "$m" "$py" <<'LAB'
set -eu
rel=$1 lab=$2 e=$3 m=$4 py=$5
ip link set lo up
ip link add lab0 type veth peer name lab1
ip addr add 192.0.2.10/24 dev lab0
ip link set lab0 up
ip link set lab1 up
pids=
trap 'for p in $pids; do kill -TERM "$p" 2>/dev/null || true; done; for p in $pids; do wait "$p" || true; done' EXIT
wait_http() {
  n=0
  while ! curl -fsS --max-time 2 "$1" > "$2" 2>/dev/null; do
    n=$((n+1))
    if [ "$n" -ge 60 ]; then echo "Failed readiness: $1" >&2; return 1; fi
    sleep 1
  done
}
export SPIRA_MATTER_INTERFACE=lab0 SPIRA_MATTER_NODE_SUFFIX=team-lab
export PYTHONPATH="$rel/src" PYTHONDONTWRITEBYTECODE=1
MATTER_PATH_ROOT="$lab/plug" MATTER_STORAGE_PATH="$lab/plug" node "$m/device.mjs" >"$e/device.log" 2>&1 &
pids="$pids $!"
wait_http http://127.0.0.1:8721/ "$e/plug-start.json"
MATTER_PATH_ROOT="$lab/controller" MATTER_STORAGE_PATH="$lab/controller" SPIRA_MATTER_EVENT_JOURNAL="$lab/controller/events.jsonl" SPIRA_MATTER_PEERS='[{"id":"plug","passcode":20202021,"discriminator":3840}]' node "$m/controller.mjs" >"$e/controller.log" 2>&1 &
pids="$pids $!"
wait_http http://127.0.0.1:8720/inventory "$e/inventory.json"
MQTT_ENABLED=0 MATTER_URL=http://127.0.0.1:8720 HOME_DB="$lab/home.db" "$py" -m uvicorn spira_home.app:app --host 127.0.0.1 --port 8750 >"$e/home.log" 2>&1 &
pids="$pids $!"
wait_http http://127.0.0.1:8750/health "$e/home-health.json"
"$py" - "$e/inventory.json" <<'PY' >"$e/result.json"
import json
import sys
import time
from pathlib import Path
import httpx
h=httpx.Client(base_url='http://127.0.0.1:8750', timeout=15)
p=httpx.Client(base_url='http://127.0.0.1:8721', timeout=10)
c=httpx.Client(base_url='http://127.0.0.1:8720', timeout=10)
for _ in range(120):
    inventory=c.get('/inventory').raise_for_status().json()
    if len(inventory['targets']) == 1 and all(p['commissioned'] for p in inventory['peers']) and inventory['subscriptions']:
        break
    time.sleep(.25)
else:
    raise AssertionError(inventory)
Path(sys.argv[1]).write_text(json.dumps(inventory))
assert any(x['target']=='matter:plug' for x in h.get('/capabilities').raise_for_status().json())
before=p.get('/').raise_for_status().json()['power']
body={'episode_id':'matter-lab','plan_id':'matter-lab','plan_version':1,'task_id':'plug','target':'matter:plug','desired':{'power':not before},'valid_until':time.time()+120,'temporary':True,'restore':True}
key=h.post('/v1/tasks',json=body).raise_for_status().json()['id']
def wait(expected):
    for _ in range(100):
        row=next(x for x in h.get('/v1/tasks').raise_for_status().json() if x['id']==key)
        if row['status']==expected:
            return row
        time.sleep(.1)
    raise AssertionError(row)
row=wait('VERIFIED')
result=row['result']
assert result['command_status']['status']=='ACCEPTED' and result['observation']['values']['power'] is (not before), row
after=p.get('/').raise_for_status().json()['power']
assert after is (not before)
assert p.post('/toggle').raise_for_status().json()['power'] is before
override=wait('OVERRIDDEN')
h.post('/v1/control',json={'action':'end','plan_id':'matter-lab'}).raise_for_status()
last=c.get('/read',params={'target':'matter:plug'}).raise_for_status().json()
assert last['values']['power'] is before and last['provenance']=='UNKNOWN', last
print(json.dumps({'status':'PASS','scope':'virtual Matter in isolated S10 network namespace','commissioned_virtual_nodes':len(inventory['peers']),'discovered_targets':len(inventory['targets']),'subscriptions':len(inventory['subscriptions']),'command':result['command_status']['status'],'independent_readback':result['effect_status'],'out_of_band_override':override['status'],'provenance':last['provenance'],'ended_preserving_outside_value':True},indent=2))
PY
cat "$e/result.json"
LAB
