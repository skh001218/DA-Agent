'use strict';
let preparingRequest = null;
let requestPolling = false;
const taskNames = {calculation: '지표 계산', review: '분석 오류 수정', design: '업무 요청 구체화'};
const levelNames = {beginner: '초급', intermediate: '중급', advanced: '고급'};

async function trainingHome() {
  const recommendation = await api('/api/training/recommendation');
  $('#recommendation').textContent = `다음 훈련 제안: ${levelNames[recommendation.difficulty]} ${taskNames[recommendation.task_kind]} · ${recommendation.selection_reason}`;
  $('#request-training').onclick = () => busy($('#request-training'), async () => {
    const message = $('#training-request').value.trim();
    if (!message) throw new Error('연습하고 싶은 내용을 입력해주세요.');
    notice();
    preparingRequest = uid();
    $('#request-status').textContent = '요청 접수 중…';
    await post('/api/training/requests', {request_id: preparingRequest, message, difficulty: $('#request-level').value, task_kind: $('#request-kind').value});
    await pollTraining(preparingRequest);
  });
  $('#cancel-training').onclick = () => busy($('#cancel-training'), async () => {
    if (preparingRequest) await post(`/api/training/requests/${encodeURIComponent(preparingRequest)}/cancel`, {});
  });
  $('#load-metrics').onclick = () => busy($('#load-metrics'), async () => {
    const metrics = await api('/api/training/metrics');
    const {events, ...summary} = metrics;
    $('#metrics').textContent = pretty(summary);
  });
  $('#export-metrics').onclick = () => busy($('#export-metrics'), async () => {
    await api('/api/training/metrics/export');
    const link = el('a'); link.href = '/api/training/metrics/export'; link.download = 'da-training-events.json';
    document.body.append(link); link.click(); link.remove();
    notice('운영 이벤트 파일을 요청했습니다. 브라우저의 다운로드 목록을 확인하세요.');
  });
  const id = new URLSearchParams(location.search).get('request');
  if (id && !requestPolling) { preparingRequest = id; await pollTraining(id); }
}

async function pollTraining(id) {
  requestPolling = true; $('#cancel-training').hidden = false;
  history.replaceState(null, '', `/?request=${encodeURIComponent(id)}`);
  const labels = {accepted: '요청 접수', planning: '과제 설계', validating: '데이터와 실제 계산 검증', ready: '출제 완료', failed: '출제 실패', cancelled: '취소', interrupted: '작업 중단', needs_clarification: '요청 확인 필요'};
  try {
    for (let round = 0; round < 120; round++) {
      const result = await api(`/api/training/requests/${encodeURIComponent(id)}`);
      $('#request-status').textContent = `${labels[result.status] || result.status}${result.error ? ' · ' + result.error : ''}\n${(result.states || []).map(x => labels[x.status] || x.status).join(' → ')}`;
      if (result.status === 'ready') { await openAttempt(result.attempt_id); return; }
      if (['failed', 'cancelled', 'interrupted', 'needs_clarification'].includes(result.status)) return;
      await new Promise(resolve => setTimeout(resolve, 500));
    }
    $('#request-status').textContent += '\n준비 시간이 길어지고 있습니다. 요청 상태를 유지했습니다. 새로고침하면 다시 확인합니다.';
  } finally { requestPolling = false; $('#cancel-training').hidden = true; }
}

function trainingOpen(attempt) {
  const requested = attempt.contract_version === 'request-v1';
  $('#business-facts').hidden = !requested;
  $('#business-result').textContent = '';
  $('#automatic-coaching').checked = false;
  $('#automatic-coaching').disabled = !requested;
  $('#temporary-question').checked = false;
  $('#task-policy').textContent = requested ? `${attempt.problem.difficulty_reason}\n${attempt.problem.selection_reason}\n${attempt.problem.data_preparation}\n${attempt.problem.evaluation_status}\n배점: ${Object.entries(attempt.problem.weights).map(([key, weight]) => `${({problem_definition:'문제 정의',analysis_approach:'분석 접근',sql_accuracy:'계산 정확성',interpretation:'해석',next_actions:'다음 행동'})[key]} ${weight}`).join(' · ')}` : '';
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
  if (state.attempt.contract_version === 'request-v1' && $('#automatic-coaching').checked && result.status === 'success') {
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
