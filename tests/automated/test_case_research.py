import copy
import json

import httpx
import pytest
from da_agent import case_research
from da_agent.errors import DomainError
from da_agent.task_contracts import RequestV2
from test_api_provider import provider, completed
from test_task_generation_v2 import v2_db_client


def research_result():
    text = '공개 사례는 이용자 온보딩의 전환율을 분석하여 서비스 경험을 개선하는 업무를 설명합니다.'
    return {'state':'completed','model':'fixture-search','text':text,'grounding':{
        'webSearchQueries':['official analytics business case study'],
        'groundingChunks':[{'web':{'uri':'https://example.org/case-study','title':'공개 분석 사례'}}],
        'groundingSupports':[{'segment':{'text':text},'groundingChunkIndices':[0]}]}}


def selection_result(topic='서비스 온보딩 전환 분석'):
    return {'state':'completed','model':'fixture-selection','text':json.dumps({'scope':'focused',
      'candidates':[{'topic':topic,'business_problem':'실제 공개 근거를 참고하여 서비스 전환 단계의 업무 문제를 검토합니다.',
       'analysis_question':'이용자 그룹별 전환 결과를 비교하여 개선 우선순위를 확인합니다.',
       'decision':'비교 결과와 한계를 바탕으로 운영 개선의 우선순위를 제안합니다.',
       'source_ids':['source-1'],'overlaps_recent':False,'novelty_reason':'현재 요청한 주제를 유지하면서 공개 자료로 판단을 연습합니다.'}],
      'selected_index':0,'reason':'현재 요청과 일치하는 공개 근거를 바탕으로 검산 가능한 연습을 선정했습니다.'},ensure_ascii=False)}


def test_provider_search_is_separate_and_returns_actual_grounding(tmp_path):
    sent=[]
    grounded=research_result()
    payload=completed(grounded['text'])
    payload['candidates'][0]['groundingMetadata']=grounded['grounding']
    p=provider(tmp_path,lambda request:(sent.append(json.loads(request.content)) or httpx.Response(200,json=payload)))
    result=p.research([{'role':'user','content':'실무 사례 검색'}])
    assert sent[0]['tools']==[{'googleSearch':{}}]
    assert 'responseJsonSchema' not in sent[0]['generationConfig']
    assert case_research.grounded_sources(result)['sources'][0]['url']=='https://example.org/case-study'
    p.review([{'role':'user','content':'일반 코칭'}])
    assert 'tools' not in sent[1]


@pytest.mark.parametrize('change',['queries','supports','fake_text','bad_index','unsafe_url','failure'])
def test_search_does_not_publish_unverified_evidence(change):
    result=research_result()
    if change=='queries':result['grounding']['webSearchQueries']=[]
    if change=='supports':result['grounding']['groundingSupports']=[]
    if change=='fake_text':result['grounding']['groundingSupports'][0]['segment']['text']='모델이 만든 별개의 주장'
    if change=='bad_index':result['grounding']['groundingSupports'][0]['groundingChunkIndices']=[True,-1,100]
    if change=='unsafe_url':result['grounding']['groundingChunks'][0]['web']['uri']='https://127.0.0.1/internal'
    if change=='failure':result={'state':'error','reason':'api_rate_limited'}
    with pytest.raises(DomainError):case_research.grounded_sources(result)


def test_selection_rejects_fabricated_source_and_repeated_broad_candidates():
    research=case_research.grounded_sources(research_result())
    value=json.loads(selection_result()['text'])
    value['candidates'][0]['source_ids']=['fabricated']
    with pytest.raises(DomainError):case_research.select_case({'state':'completed','text':json.dumps(value)},research,[])
    value=json.loads(selection_result()['text']);value['scope']='broad'
    with pytest.raises(DomainError):case_research.select_case({'state':'completed','text':json.dumps(value)},research,[])


def test_broad_selection_prefers_new_topic_and_pins_sources():
    research=case_research.grounded_sources(research_result())
    value=json.loads(selection_result()['text']);value['scope']='broad'
    base=value['candidates'][0]
    value['candidates']=[dict(copy.deepcopy(base),topic=topic,overlaps_recent=False) for topic in ['보스 레이드 성공률','광고 유입 효율','고객 지원 운영']]
    case=case_research.select_case({'state':'completed','text':json.dumps(value)},research,[{'source_topic':'보스 레이드 성공률'}])
    assert case['topic']=='광고 유입 효율'
    assert case['sources']==research['sources']
    assert 'candidates' not in case_research.public_case(case)
    for candidate in value['candidates']:candidate['overlaps_recent']=True
    with pytest.raises(DomainError,match='최근 주제'):case_research.select_case({'state':'completed','text':json.dumps(value)},research,[])


def test_short_request_prompt_preserves_current_intent_and_grounded_case():
    from da_agent.adaptive_tasks import planning_messages,PLANNING_LIMIT
    request=RequestV2(contract_version='request-v2',request_id='test',message='실무 문제를 분석하고 싶어',difficulty='beginner')
    research=case_research.grounded_sources(research_result())
    case=case_research.select_case(selection_result(),research,[])
    messages=planning_messages(request,[{'domain':'보스 레이드'}],case)
    assert PLANNING_LIMIT==8
    assert '상세 상황 미입력만으로 clarify하지 마세요' in messages[0]['content']
    assert '보스/스테이지 성공률' not in messages[0]['content']
    assert '반드시 groups=[{name:sample' not in messages[0]['content']
    assert json.loads(messages[1]['content'])['source_case']['topic']==case['topic']


def test_case_selection_json_schema_is_enabled_without_search(tmp_path):
    sent=[]
    p=provider(tmp_path,lambda request:(sent.append(json.loads(request.content)) or httpx.Response(200,json=completed('{}'))))
    request=RequestV2(contract_version='request-v2',request_id='test',message='실무 분석')
    p.select_case(case_research.selection_messages(request,case_research.grounded_sources(research_result()),[]))
    assert sent[0]['generationConfig']['responseJsonSchema']['title']=='Selection'
    assert 'tools' not in sent[0]


def test_database_search_failure_and_source_pinning(v2_db_client):
    import uuid
    from test_adaptive_tasks import bot_recipe
    client,p=v2_db_client
    body={'contract_version':'request-v2','request_id':str(uuid.uuid4()),'message':'비정상 이용자 조사 실무 문제',
          'difficulty':'intermediate','intentional_repeat':True}
    p.research=lambda messages:{'state':'error','reason':'api_rate_limited'}
    client.post('/api/training/requests',json=body)
    failed=client.get('/api/training/requests/'+body['request_id']).json()
    assert failed['status']=='failed' and failed['error_code']=='research_failed'
    assert failed['attempt_id'] is None and failed['planning_calls']==1
    p.research=lambda messages:research_result()
    p.select_case=lambda messages:selection_result('비정상 이용자 행동 조사')
    def review(messages):
        payload=json.loads(messages[1]['content'])
        assert payload['source_case']['topic']=='비정상 이용자 행동 조사'
        return {'state':'completed','text':json.dumps(bot_recipe() if 'schema' in payload else {'aligned':True,'issues':[],
            'quality_dimensions':{k:'pass' for k in ('business_context','evidence_sufficiency','difficulty_fit','evaluation_alignment')}})}
    p.review=review
    retried=client.post('/api/training/requests/'+body['request_id']+'/retry',json={'action_id':str(uuid.uuid4()),'expected_revision':0})
    assert retried.status_code==200
    ready=client.get('/api/training/requests/'+body['request_id']).json()
    assert ready['status']=='ready',ready
    attempt=client.get('/api/attempts/'+ready['attempt_id']).json()
    assert attempt['problem']['source_case']['sources'][0]['url']=='https://example.org/case-study'
    assert attempt['problem']['semantic_signature']['source_topic']=='비정상 이용자 행동 조사'
    assert ready['planning_calls']==5
    assert '원본 데이터나 수치를 재현한 것이 아닙니다' in attempt['problem']['description']
    assert '실제 회사 사례를 인용한 것이 아닌' not in attempt['problem']['description']


def test_database_cancel_search_does_not_start_selection(v2_db_client):
    import uuid
    client,p=v2_db_client
    rid=str(uuid.uuid4());selections=[]
    def research(messages):
        assert client.post('/api/training/requests/'+rid+'/cancel',json={}).status_code==200
        return research_result()
    p.research=research
    p.select_case=lambda messages:(selections.append(messages) or selection_result())
    client.post('/api/training/requests',json={'contract_version':'request-v2','request_id':rid,'message':'실무 문제 분석'})
    value=client.get('/api/training/requests/'+rid).json()
    assert value['status']=='cancelled' and value['attempt_id'] is None and not selections
