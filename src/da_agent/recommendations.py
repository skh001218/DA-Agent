"""Reproducible, source-backed next-task candidates from available capabilities."""
import hashlib
import json
from .capabilities import list_capabilities
from .store import now

VERSION='history-recommendation-v2'

def same_thinking(a,b):
    keys=('domain','goal','required_judgment','situation','ambiguity','evaluation')
    return bool(a and b) and all(a.get(k) is not None and a.get(k)==b.get(k) for k in keys)

def recommend(store, explicit=None):
    from .learning_state import get_state
    state=get_state(store)
    preferences=state.get('preferences',{})
    explicit=explicit or {}
    recent=store.list()[:5]
    observations=[o for o in state.get('observations',[]) if o.get('valid',True) and not state.get('overrides',{}).get(o['observation_id'],{}).get('disagree')]
    candidates=[]
    level=explicit.get('difficulty') or preferences.get('level') or 'intermediate'
    if level=='auto': level='intermediate'
    capabilities=list_capabilities(store)
    approved=[c for c in capabilities if c['status']=='approved']
    pool=approved or [c for c in capabilities if c['status']=='draft' and c['task_kind'] in ('calculation','review','design')]
    for cap in pool:
        for scenario in (cap['scenarios'] if approved else ['existing-access']):
            sig={'domain':'access','goal':cap['goal'],'required_judgment':cap['task_kind'],'format':cap['task_kind'],'situation':'s-'+hashlib.sha256(scenario.encode()).hexdigest()[:12],'ambiguity':level,'evaluation':'definition-comparison-limits' if cap['task_kind'] in ('design','investigation') else 'unit-observation-calculation'}
            repetitions=sum(same_thinking(sig,a.get('semantic_signature')) for a in recent)
            candidate={'candidate_id':cap['capability_id']+'/'+scenario,'capability_id':cap['capability_id'],'task_kind':cap['task_kind'],'difficulty':level,'goal':cap['goal'],'semantic_signature':sig,'duplicate_count':repetitions,'available_for_generation':cap['status']=='approved'}
            candidate['preference_match']=cap['task_kind']==explicit.get('task_kind') or cap['goal']==(explicit.get('goal') or preferences.get('goal'))
            candidate['explicit_match']=cap['task_kind']==explicit.get('task_kind') if explicit.get('task_kind') not in (None,'auto') else True
            competencies={'calculation':{'metrics_aggregation'},'review':{'metrics_aggregation','evidence_interpretation'},'design':{'problem_definition','analysis_design'},'investigation':{'evidence_interpretation','limitations_next_actions'}}
            candidate['evidence_match']=any(o.get('competency') in competencies[cap['task_kind']] and type(o.get('criterion_level')) is int and o['criterion_level']<3 for o in observations)
            candidates.append(candidate)
    if not candidates:
        from .errors import DomainError
        raise DomainError('capability_unavailable','사용 가능한 추천 규칙이 없습니다. 운영 화면에서 규칙 상태를 확인하세요.',409)
    candidates.sort(key=lambda c:(not c['explicit_match'],not c['preference_match'],not c['evidence_match'],c['duplicate_count'],c['candidate_id']))
    chosen=candidates[0]
    recent_times={c['candidate_id']:next((a.get('started_at','') for a in recent if same_thinking(c['semantic_signature'],a.get('semantic_signature'))),'') for c in candidates}
    candidates.sort(key=lambda c:(not c['explicit_match'],not c['preference_match'],not c['evidence_match'],c['duplicate_count'],recent_times[c['candidate_id']],c['candidate_id']))
    chosen=candidates[0]
    basis={'state_revision':state['state_revision'],'recent_ids':[a['attempt_id'] for a in recent],'candidates':candidates,'version':VERSION}
    rid='rec-'+hashlib.sha256(json.dumps(basis,sort_keys=True).encode()).hexdigest()[:24]
    reason='사용자 선호 수준을 유지하고 최근 5개에서 요구 판단·관측 상황이 덜 반복된 후보를 선택했습니다.'
    if not observations: reason+=' 수행 근거는 미관측이며 수준 변경을 제안하지 않습니다.'
    else: reason+=' 시험 운영 관측은 잠정 근거로 사용하며 도움 사용만으로 감점하거나 수준을 낮추지 않습니다.'
    if not approved: reason+=' 신규 생성 규칙은 승인 대기이므로 기존 검증 데이터 재사용 후보를 제안합니다.'
    if chosen['duplicate_count']: reason+=' 같은 판단의 반복 가능성이 있습니다. 다른 목표를 선택하거나 의도적 반복으로 연습할 수 있습니다.'
    return dict(recommendation_id=rid,created_at=now(),rules_version=VERSION,input_state_revision=state['state_revision'],recent_task_ids=basis['recent_ids'],candidates=candidates,
                difficulty=level,task_kind=chosen['task_kind'],goal=chosen['goal'],selection_reason=reason,reason=reason,evidence_ids=[o['observation_id'] for o in observations],
                provisional=True,repetition={'algorithm':'semantic-signature-v2','duplicate_count':chosen['duplicate_count'],'intentional_repeat':bool(explicit.get('intentional_repeat')),'human_verdict':None},supported_domain='access')
