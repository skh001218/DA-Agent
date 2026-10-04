'use strict';
let preparingRequest = null;
let requestPolling = false;
const taskNames = {calculation: '지표 계산', review: '분석 오류 수정', design: '업무 요청 구체화', investigation: '접속 현상 조사'};
const levelNames = {beginner: '초급', intermediate: '중급', advanced: '고급'};

let currentRequest = null;
let currentRecommendation = null;
let currentLearningState = null;
async function loadLearningState() {
  const data = await api('/api/learning-state'); currentLearningState = data;
  $('#learning-state').textContent = `수정 번호: ${data.state_revision}\n관측 ${data.observations?.length || 0}건 · 자동 관측은 확정 판단이 아닙니다.\n역량별 관측: ${Object.entries(data.competencies || {}).map(([key,value])=>`${key}: ${typeof value==='string'?value:value.certainty || value.status || '잠정'}`).join(' · ')}`;
  $('#learning-level').value = data.preferences?.level || 'auto';
  $('#learning-goal').value = data.preferences?.goal || '';
  $('#learning-overrides').replaceChildren();
  for (const observation of data.observations || []) {
    const group = el('div'); group.dataset.observation = observation.observation_id;
    group.append(el('p', `${observation.competency} · 출처 훈련 ${observation.attempt_id} · ${observation.certainty} · ${observation.confirmed ? '확정' : '자동 추정'}`));
    const label = el('label'); const disagree = el('input'); disagree.type='checkbox'; disagree.className='learning-disagree'; disagree.checked=Boolean(data.overrides?.[observation.observation_id]?.disagree); label.append(disagree, document.createTextNode('이 관측에 이견 있음'));
    const reasonLabel = el('label','이견의 이유'); const reason=el('textarea'); reason.className='learning-reason'; reason.value=data.overrides?.[observation.observation_id]?.reason || ''; reasonLabel.append(reason); group.append(label,reasonLabel); $('#learning-overrides').append(group);
  }
  if (!(data.observations || []).length) $('#learning-overrides').append(el('p','미관측 · 아직 확인된 학습 근거가 없습니다.','muted'));
}
async function startTraining(recommended = false) {
  const message = $('#training-request').value.trim();
  if (!message && !recommended) throw new Error('연습하고 싶은 내용을 입력해주세요.');
  notice(); preparingRequest = uid(); $('#request-status').textContent = '요청 접수 중…';
  const body = {contract_version:'request-v2', request_id:preparingRequest,
    message: recommended ? `접속 데이터로 ${levelNames[currentRecommendation.difficulty]} ${taskNames[currentRecommendation.task_kind]} 연습` : message,
    difficulty:recommended ? currentRecommendation.difficulty : $('#request-level').value,
    task_kind:recommended ? currentRecommendation.task_kind : $('#request-kind').value, domain:$('#request-domain').value,
    data_mode:$('#request-data').value, user_count:200, intentional_repeat:$('#intentional-repeat').checked};
  for (const [key, id] of [['goal','request-goal'],['sql_level','request-sql-level'],['time_condition','request-time']]) if ($('#'+id).value.trim()) body[key] = $('#'+id).value.trim();
  if (recommended && currentRecommendation.recommendation_id) body.recommendation_id = currentRecommendation.recommendation_id;
  try { await post('/api/training/requests', body); await pollTraining(preparingRequest); }
  catch (error) { $('#request-status').textContent = `요청 실패 · 입력 유지: ${error.message}`; throw error; }
}
async function trainingHome() {
  const recommendation = await api('/api/training/recommendation'); currentRecommendation = recommendation;
  $('#recommendation').textContent = `다음 훈련 제안: ${levelNames[recommendation.difficulty]} ${taskNames[recommendation.task_kind]} · ${recommendation.reason || recommendation.selection_reason}\n근거: ${recommendation.evidence_ids?.length ? '저장된 관측 '+recommendation.evidence_ids.length+'건 (학습 상태에서 출처 확인)' : '관측 근거 없음'} · ${recommendation.provisional ? '잠정 추천' : '이력 기반 추천'}\n다른 후보: ${(recommendation.candidates || []).filter(x=>x.task_kind!==recommendation.task_kind).slice(0,3).map(x=>`${levelNames[x.difficulty]} ${taskNames[x.task_kind]} · 최근 반복 ${x.duplicate_count || 0}회`).join(' / ') || '다른 지원 후보 없음'}`;
  try {
    const capabilities = await api('/api/training/capabilities');
    $('#capability-status').textContent = `지원 상태: ${(capabilities.capabilities || []).map(x => `${x.title}: ${x.status}`).join(' · ')}\n한도: ${pretty(capabilities.limits || {})}`;
    const generated = $('#request-data').querySelector('[value="generated"]');
    generated.disabled = !capabilities.generated_data_enabled;
    generated.textContent = generated.disabled ? '생성 데이터 · 승인 또는 지원 확인 필요' : '생성 데이터';
  } catch (error) { $('#capability-status').textContent = `지원 상태 확인 실패: ${error.message} · 기존 데이터로 요청할 수 있습니다.`; }
  try { await loadLearningState(); } catch (error) { $('#learning-state').textContent = `학습 상태 조회 실패: ${error.message}`; }
  $('#request-training').onclick = () => busy($('#request-training'), () => startTraining());
  $('#start-recommendation').onclick = () => busy($('#start-recommendation'), () => startTraining(true));
  $('#change-recommendation').onclick = () => { $('#request-level').value = recommendation.difficulty; $('#request-kind').value = recommendation.task_kind; $('#request-kind').focus(); notice('추천 수준과 주제를 불러왔습니다. 원하는 값으로 바꾸고 훈련 준비를 선택하세요.'); };
  $('#refresh-learning').onclick = () => busy($('#refresh-learning'), loadLearningState);
  $('#save-learning').onclick = () => busy($('#save-learning'), async () => {
    if (!currentLearningState) throw new Error('먼저 최신 학습 상태를 확인하세요.');
    if ([...$('#learning-overrides').querySelectorAll('[data-observation]')].some(x=>x.querySelector('.learning-disagree').checked && !x.querySelector('.learning-reason').value.trim())) throw new Error('이견을 선택한 관측에는 이유를 입력하세요.');
    try {
      await api('/api/learning-state', {method:'PATCH',body:JSON.stringify({expected_revision:currentLearningState.state_revision, preferences:{level:$('#learning-level').value,goal:$('#learning-goal').value}, overrides:[...$('#learning-overrides').querySelectorAll('[data-observation]')].filter(x=>x.querySelector('.learning-disagree').checked || x.querySelector('.learning-reason').value.trim()).map(x=>({observation_id:x.dataset.observation,disagree:x.querySelector('.learning-disagree').checked,reason:x.querySelector('.learning-reason').value}))})});
      await loadLearningState(); notice('학습 상태를 저장했습니다.');
    } catch(error) { notice(`학습 상태 저장 실패 · 입력 유지: ${error.message}${error.status === 409 ? ' · 최신 상태 확인 후 다시 저장하세요.' : ''}`); }
  });
  $('#cancel-training').onclick = () => busy($('#cancel-training'), async () => { if (preparingRequest) { await post(`/api/training/requests/${encodeURIComponent(preparingRequest)}/cancel`, {}); if (!requestPolling) await pollTraining(preparingRequest); } });
  $('#send-clarification').onclick = () => busy($('#send-clarification'), async () => {
    if (!currentRequest) return;
    const message = $('#clarification-message').value.trim(); if (!message) throw new Error('추가 확인 답변을 입력하세요.');
    await post(`/api/training/requests/${encodeURIComponent(preparingRequest)}/clarify`, {action_id:uid(),expected_revision:currentRequest.revision,message});
    await pollTraining(preparingRequest);
  });
  $('#retry-training').onclick = () => busy($('#retry-training'), async () => {
    await post(`/api/training/requests/${encodeURIComponent(preparingRequest)}/retry`, {action_id:uid(),expected_revision:currentRequest.revision}); await pollTraining(preparingRequest);
  });
  $('#load-metrics').onclick = () => busy($('#load-metrics'), async () => { const {events,...summary} = await api('/api/training/metrics'); $('#metrics').textContent = pretty(summary); });
  $('#export-metrics').onclick = () => { location.href = '/api/training/metrics/export'; };
  const id = new URLSearchParams(location.search).get('request');
  if (id && !requestPolling) { preparingRequest = id; await pollTraining(id); }
}
async function pollTraining(id) {
  requestPolling = true; $('#request-training').disabled = true; $('#start-recommendation').disabled = true;
  $('#clarification').hidden = true; $('#retry-training').hidden = true;
  history.replaceState(null, '', `/?request=${encodeURIComponent(id)}`);
  const labels = {accepted:'요청 접수',planning:'과제 설계',preparing_data:'데이터 준비',validating:'데이터와 실제 계산 검증',ready:'출제 완료',failed:'출제 실패',cancelled:'취소',interrupted:'작업 중단',needs_clarification:'요청 확인 필요'};
  try {
    for (let round=0; round<120; round++) {
      const result = await api(`/api/training/requests/${encodeURIComponent(id)}`); currentRequest = result;
      if (!$('#training-request').value && (result.message || result.request?.message)) $('#training-request').value=result.message || result.request.message;
      const started = Date.parse(result.created_at || result.states?.[0]?.at || result.states?.[0]?.timestamp);
      const elapsed = Number.isFinite(started) ? ` · 경과 ${Math.max(0,Math.floor((Date.now()-started)/1000))}초` : '';
      $('#request-status').textContent = `${labels[result.status] || result.status}${elapsed} · 수정 번호 ${result.revision ?? '없음'}${result.error ? '\n'+pretty(result.error) : ''}\n${(result.states || []).map(x => labels[x.status] || x.status).join(' → ')}${result.questions?.length ? '\n'+result.questions.join('\n') : result.clarification ? '\n'+pretty(result.clarification) : ''}`;
      $('#cancel-training').hidden = ['ready','failed','cancelled','interrupted'].includes(result.status);
      if (result.status === 'ready') { await openAttempt(result.attempt_id); return; }
      if (result.status === 'needs_clarification') { $('#clarification').hidden=false; $('#clarification-message').focus(); return; }
      if (['failed','cancelled','interrupted'].includes(result.status)) { $('#retry-training').hidden=!result.retry_allowed; return; }
      await new Promise(resolve=>setTimeout(resolve,500));
    }
    $('#request-status').textContent += '\n준비 시간이 길어지고 있습니다. 요청 상태를 유지했습니다. 새로고침하면 다시 확인합니다.';
  } catch(error) { $('#request-status').textContent += `\n상태 조회 실패 · 요청과 입력 유지: ${error.message}`; throw error; }
  finally { requestPolling=false; $('#request-training').disabled=false; $('#start-recommendation').disabled=false; }
}

function trainingOpen(attempt) {
  const requested = ['request-v1', 'request-v2'].includes(attempt.contract_version);
  $('#business-facts').hidden = !requested;
  $('#business-result').textContent = '';
  $('#automatic-coaching').checked = false;
  $('#automatic-coaching').disabled = !requested;
  $('#temporary-question').checked = false;
  $('#task-policy').textContent = requested ? `${[attempt.problem.difficulty_reason,attempt.problem.selection_reason,attempt.problem.data_preparation,attempt.problem.evaluation_status].filter(Boolean).join('\n')}\n배점: ${Object.entries(attempt.problem.weights || {}).map(([key, weight]) => `${({problem_definition:'문제 정의',analysis_approach:'분석 접근',sql_accuracy:'계산 정확성',interpretation:'해석',next_actions:'다음 행동'})[key]} ${weight}`).join(' · ')}` : '';
  $('#discoveries').placeholder = attempt.task_kind === 'design' ? '정의한 질문·지표·비교 기준·검증 계획을 작성하세요. SQL 제출은 필수가 아닙니다.' : '실제 근거와 계산·검토 결과를 작성하세요.';
  $('#report-evidence-policy').textContent = attempt.task_kind === 'design' ? '분석 설계 과제는 SQL 실행·저장이 필수가 아닙니다. 공개 자료를 바탕으로 판단 이유와 검증 계획을 설명해주세요.' : '근거 없이도 제출할 수 있지만 해당 주장은 근거 부족으로 표시됩니다. 저장한 성공 실행만 연결할 수 있습니다.';
  renderConversation(attempt.messages || []);
  $('#business-facts').onclick = () => busy($('#business-facts'), async () => {
    const facts = await api(attemptPath('/business-facts'));
    renderProblemMetadata(facts);
    $('#business-result').textContent = '출제 전에 고정한 업무 조건입니다. 이 자료로 원인을 확정할 수 있는 것은 아닙니다.';
  });
}

function renderConversation(messages) {
  $('#conversation-history').replaceChildren();
  for (const message of messages) {
    const node = el('article');
    node.append(el('p', `질문: ${message.message}`), el('p', message.response.feedback || message.response.error?.message || ''), el('p', '저장된 대화', 'muted'));
    $('#conversation-history').append(node);
  }
}

async function requestConversation(message, reference = '', trigger = 'question', actionId = uid()) {
  const originalAttempt = state.attempt.attempt_id;
  const isTemporary = reference.startsWith('temp:');
  const result = await post(attemptPath('/conversation'), {action_id: actionId, message, trigger,
    execution_id: isTemporary ? reference.slice(5) : null, saved_execution_id: !isTemporary && reference ? reference : null,
    transient: $('#temporary-question').checked || isTemporary});
  if (state.attempt?.attempt_id !== originalAttempt) return result;
  $('#coach-result').textContent = `${pretty(result.feedback || result.error?.message || result)}\n${result.transient ? '임시 질문·응답 · 새로고침 후 사라집니다.' : '일반 대화 · 저장됨'} `;
  if (result.status === 'completed' && !result.transient) {
    (state.attempt.messages ||= []).push({message, response: result}); renderConversation(state.attempt.messages);
  }
  return result;
}

async function afterTrainingExecution(result) {
  renderSaved();
  if (['request-v1', 'request-v2'].includes(state.attempt.contract_version) && $('#automatic-coaching').checked && result.status === 'success') {
    const attemptId = state.attempt.attempt_id;
    $('#coach-result').textContent = '결과에 맞는 다음 행동을 확인 중…';
    try { await requestConversation('이 실제 실행 결과와 현재 풀이에서 다음에 확인할 행동 하나를 제안해주세요.', `temp:${result.execution_id}`, 'sql_result', `sql-${result.execution_id}`); }
    catch (error) { if (state.attempt?.attempt_id === attemptId) $('#coach-result').textContent = `자동 코칭 실패: ${error.message} · SQL 결과는 유지됩니다.`; }
  }
}

function addTrainingDelete(card, attempt) {
  const button = el('button', '훈련 삭제');
  button.onclick = () => busy(button, async () => {
    if (!confirm('이 훈련의 대화·저장 SQL·보고서·리뷰·연결 기록을 삭제할까요? 공유 데이터는 유지됩니다. 내보낸 파일·백업은 별도로 관리해주세요.')) return;
    await api(`/api/attempts/${encodeURIComponent(attempt.attempt_id)}`, {method: 'DELETE', body: '{}'});
    await home(); notice('훈련과 연결 기록을 삭제했습니다.');
  });
  card.append(button);
}
