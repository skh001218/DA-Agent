import datetime
import json
import re
import subprocess
from pathlib import Path

run=subprocess.run(['docker','exec','da-agent-v3-verification','python','scripts/verify_request_training.py','tests'],capture_output=True,text=True,encoding='utf-8')
output=run.stdout+run.stderr
result=dict(executed_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),actual_db=True,isolated_recorder_schema=True,source_bind_mount=True,existing_app_preserved=True,exit_code=run.returncode,counts={key:int(m.group(1)) for key in ('passed','failed','skipped','warnings') if (m:=re.search(r'(\d+) '+key,output))},raw_output=output)
Path('tests/verification-evaluation-v3-db-2026-10-05.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in result.items() if k!='raw_output'}))
raise SystemExit(run.returncode)
