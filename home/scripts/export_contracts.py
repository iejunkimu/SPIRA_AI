import json
from pathlib import Path
from spira_home.models import Task, Event, Capability, Observation, Receipt
from spira_home.app import app
from spira_home.bridge import Plan

for model in (Task, Event, Capability, Observation, Receipt, Plan):
    Path('contracts/'+model.__name__.lower()+'.schema.json').write_text(json.dumps(model.model_json_schema(),indent=2)+'\n')
Path('contracts/openapi.json').write_text(json.dumps(app.openapi(),indent=2)+'\n')
