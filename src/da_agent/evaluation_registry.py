"""Read/write frozen automatic-quality profiles; a missing profile holds scores."""
from copy import deepcopy
import json
import os
from pathlib import Path
from .evaluation_quality import fingerprint, quality_gate


def code_hashes():
    root=Path(__file__).parent
    names={'evaluation_quality.py','evaluation_metrics.py','evaluation_registry.py',
           'discord_education.py','discord_verification.py','discord_provider.py','api_provider.py'}
    import hashlib
    return {name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in sorted(names)}


def task_key(task):
    # Public definitions, including difficulty/help, can affect model judgments.
    keys=('task_id','topic','version','data_version','difficulty','schema','dictionary',
          'objective','period','metrics','timezone','rubric','help_policy','arithmetic_contract')
    return fingerprint({k:task.get(k) for k in keys})


class QualityRegistry:
    def __init__(self, root=None):
        self.root=Path(root or os.getenv('DISCORD_QUALITY_PROFILES_DIR','.local/evaluation-quality')).resolve()

    def check(self, task, model):
        profile=self.root/task_key(task)/'profile.json'
        try:
            bundle=json.loads(profile.read_text(encoding='utf-8'))
            if task_key(bundle['fixtures']['task']) != task_key(task): raise ValueError('task_changed')
            result=quality_gate(bundle['fixtures'],bundle['run'],model=model,code_hashes=code_hashes())
            return dict(result,profile_id=task_key(task),run_fingerprint=fingerprint(bundle['run']))
        except (OSError,ValueError,KeyError,TypeError):
            return {'status':'held','profile_id':task_key(task),
                    'reasons':['이 문제 정의·난이도·모델·코드에 맞는 반복 품질 검증 기록이 없거나 읽을 수 없습니다.'],
                    'scope':'등록된 문제 유형의 반복 품질 검사','human_review':'pending'}

    def register(self, fixtures, run, *, model):
        result=quality_gate(fixtures,run,model=model,code_hashes=code_hashes())
        directory=self.root/task_key(fixtures['task'])
        directory.mkdir(parents=True,exist_ok=True)
        archive=directory/(fingerprint(run)+'.json')
        bundle={'fixtures':deepcopy(fixtures),'run':deepcopy(run),'gate':result}
        encoded=json.dumps(bundle,ensure_ascii=False,indent=2)
        if not archive.exists(): archive.write_text(encoded,encoding='utf-8')
        if result['status']=='eligible':
            temporary=directory/'profile.tmp'
            temporary.write_text(encoded,encoding='utf-8'); temporary.replace(directory/'profile.json')
        return result


def with_profile_policy(result, profile):
    value=deepcopy(result)
    value['quality_profile']=deepcopy(profile)
    if profile['status']!='eligible':
        if value.get('reason'): value['individual_held_reason']=value['reason']
        value['candidate_total']=value.get('total')
        value['total']=None
        value['held']=True
        value['profile_score_hold']=True
        value['reason']='문제 유형의 반복 품질 검증이 아직 통과하지 않아 점수를 보류합니다. 분석 피드백과 검산 결과는 보존했습니다. 검증 통과 후 /submit로 다시 평가하세요.'
    return value
