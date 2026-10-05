"""Finite generation rules with explicit human approval, never executable LLM code."""
from psycopg.types.json import Jsonb
from .store import now
from .errors import DomainError

RULES_VERSION = 'access-rules-v2'
CAPABILITIES = [
    {'capability_id':'access-calculation','title':'미재접속 지표 계산','task_kind':'calculation','goal':'지표·집계','scenarios':['baseline','observation_short']},
    {'capability_id':'access-review','title':'집계 오류와 수정 검토','task_kind':'review','goal':'지표·집계 오류 검토','scenarios':['baseline','composition']},
    {'capability_id':'access-design','title':'업무 요청과 분석 설계','task_kind':'design','goal':'문제 정의·분석 설계','scenarios':['no_difference','observation_short']},
    {'capability_id':'access-investigation','title':'집단·기간 현상 조사','task_kind':'investigation','goal':'근거 해석·관측 비교','scenarios':['group_difference','period_change','composition','no_difference']},
]

def initialize(store):
    with store.connect() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS generation_approvals (capability_id text PRIMARY KEY,payload jsonb NOT NULL)')

def list_capabilities(store):
    with store.connect() as conn:
        approvals = {r['capability_id']:r['payload'] for r in conn.execute('SELECT * FROM generation_approvals')}
    return [dict(c,domain='access',tables=['users','sessions'],rules_version=RULES_VERSION,
                 supported_topic='return_observation',
                 scope='신규 유저 D1~D7 미재접속과 관측 조건, 플랫폼별 비교. 비정상 이용자 탐지·봇·부정행위 판단은 지원하지 않음.',
                 status=approvals.get(c['capability_id'],{}).get('result','draft'),approval=approvals.get(c['capability_id'])) for c in CAPABILITIES]

def approve(store, capability_id, data):
    if capability_id not in {c['capability_id'] for c in CAPABILITIES}:
        raise DomainError('not_found','생성 능력을 찾을 수 없습니다.',404)
    with store.connect() as conn:
        samples = conn.execute('SELECT sample_id,payload FROM generation_samples WHERE sample_id = ANY(%s)',(data.sample_ids,)).fetchall()
        if data.result=='approved' and (len(samples) != len(set(data.sample_ids)) or len(samples)<3 or {r['payload'].get('difficulty') for r in samples}!={'beginner','intermediate','advanced'} or any(r['payload']['capability_id']!=capability_id or r['payload']['status']!='validated' or r['payload']['rules_version']!=RULES_VERSION for r in samples)):
            raise DomainError('invalid_samples','해당 규칙으로 실제 검증한 표본 3개 이상을 선택하세요.',422)
        value=dict(data.model_dump(),approved_at=now())
        conn.execute('INSERT INTO generation_approvals VALUES(%s,%s) ON CONFLICT(capability_id) DO UPDATE SET payload=excluded.payload',(capability_id,Jsonb(value)))
    return value
