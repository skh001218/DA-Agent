"""Run host source on port 8088 without replacing the user's changing UI."""
import json
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1]
original = json.loads(subprocess.check_output(['docker','inspect','da-agent-app-1'], text=True))[0]
network = next(iter(original['NetworkSettings']['Networks']))
args = ['docker','run','-d','--name','da-agent-v3-verification','--network',network,'-p','127.0.0.1:8088:8000']
for value in original['Config']['Env']:
    args += ['--env',value]
for mount in original['Mounts']:
    args += ['--mount',f"type={mount['Type']},source={mount.get('Name') if mount['Type']=='volume' else mount['Source']},target={mount['Destination']}" + (',readonly' if not mount['RW'] else '')]
for folder in ['src','tests','scripts']:
    args += ['--mount',f'type=bind,source={root / folder},target=/app/{folder},readonly']
args += [original['Config']['Image']]
subprocess.run(args,check=True,stdout=subprocess.DEVNULL)
print('Isolated source verification app started on http://127.0.0.1:8088; existing app preserved.')
