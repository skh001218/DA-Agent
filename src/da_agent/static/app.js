'use strict';
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const state = { attempt: null, executions: [], sections: {}, revision: 0, dirty: false, generation: 0, saveTimer: null, savePromise: null, conflict: false, claimEvidence: new Map(), reportRequest: null, previousReport: null };
const uid = () => crypto.randomUUID();
function el(tag, text, className) { const node = document.createElement(tag); if (text !== undefined) node.textContent = String(text); if (className) node.className = className; return node; }
function pretty(value) { return typeof value === 'string' ? value : JSON.stringify(value, null, 2); }
function notice(message = '') { $('#notice').textContent = message; $('#notice').hidden = !message; }
async function api(path, options = {}) {
  const response = await fetch(path, { ...options, headers: { 'Content-Type': 'application/json', ...(options.headers || {}) } });
  let data; try { data = await response.json(); } catch { throw new Error('서버 응답을 읽을 수 없습니다. 입력을 유지하고 다시 시도하세요.'); }
  if (!response.ok) { const detail = data.detail || data; const error = new Error(detail.message || (typeof detail === 'string' ? detail : '요청에 실패했습니다. 다시 시도하세요.')); error.code = detail.code; error.status = response.status; throw error; }
  return data;
}
const post = (path, body) => api(path, { method: 'POST', body: JSON.stringify(body) });
const attemptPath = (suffix = '') => `/api/attempts/${encodeURIComponent(state.attempt.attempt_id)}${suffix}`;
async function busy(button, action) { if (button.disabled) return; button.disabled = true; try { await action(); } catch (error) { notice(error.message); } finally { button.disabled = false; } }
function tab(name) { $$('.tab-panel').forEach(node => { node.hidden = node.id !== name; }); $$('nav button').forEach(node => node.classList.toggle('active', node.dataset.tab === name)); }
async function authStatus() {
  try { const auth = await api('/api/auth/status'); $('#auth-status').textContent = auth.message || auth.status; const connected = ['connected', 'ready'].includes(auth.status); $('#disconnect').hidden = !connected; $('#connect').hidden = connected; }
  catch (error) { $('#auth-status').textContent = 'AI 연결 확인 실패'; }
}
async function home() {
  const [packages, attempts] = await Promise.all([api('/api/packages'), api('/api/attempts')]);
  $('#packages').replaceChildren(); $('#attempts').replaceChildren();
  for (const pack of packages.packages) for (const problem of pack.problems) {
    const card = el('article', undefined, 'card'); card.append(el('h3', problem.title), el('p', `${pack.package_id} · ${pack.release_version}`));
    const button = el('button', '새 훈련 시작', 'primary'); button.onclick = () => busy(button, async () => { if (!(await canLeave())) return; const attempt = await post('/api/attempts', { package_id: pack.package_id, release_version: pack.release_version, problem_id: problem.problem_id }); await openAttempt(attempt.attempt_id); }); card.append(button); $('#packages').append(card);
  }
  for (const attempt of attempts.attempts) { const card = el('article', undefined, 'card'); card.append(el('h3', attempt.problem?.title || attempt.problem_id), el('p', `${attempt.release_version} · 훈련 ${attempt.attempt_id}`)); const button = el('button', '이어서 분석'); button.onclick = () => busy(button, async () => { if (await canLeave()) await openAttempt(attempt.attempt_id); }); card.append(button); $('#attempts').append(card); }
  if (!attempts.attempts.length) $('#attempts').append(el('p', '아직 시작한 훈련이 없습니다.', 'muted'));
}
async function canLeave() {
  if (!state.attempt) return true;
  try { await flushDraft(); } catch (error) { notice(error.message); return false; }
  if ($('#sql').value.trim() || state.executions.some(item => !item.saved)) return confirm('미저장 SQL과 실행 결과가 사라집니다. 훈련 목록으로 이동할까요?');
  return true;
}
async function openAttempt(id) {
  const attempt = await api(`/api/attempts/${encodeURIComponent(id)}`); clearTimeout(state.saveTimer);
  state.attempt = attempt; state.executions = []; state.sections = { ...(attempt.draft?.sections || {}) }; state.revision = attempt.draft?.revision ?? 0; state.dirty = false; state.generation = 0; state.conflict = false; state.previousReport = null; state.reportRequest = null; state.claimEvidence.clear();
  $('#sql').value = ''; $('#executions').replaceChildren(); $('#coach-result').replaceChildren(); $('#coach-message').value = ''; $('#draft-error').textContent = ''; $('#resolve-conflict').hidden = true; $('#save-status').textContent = '저장됨'; $('#report-status').textContent = ''; $('#explanation').textContent = attempt.explanation_viewed ? '이 훈련에서 해설을 참조했습니다. 다시 보려면 해설 공개를 선택하세요.' : '';
  $$('[data-section]').forEach(node => { node.value = state.sections[node.dataset.section] || ''; });
  $('#home').hidden = true; $('#workspace').hidden = false; $('#problem-title').textContent = attempt.problem?.title || attempt.problem_id; $('#version').textContent = `${attempt.package_id} · ${attempt.release_version} · ${attempt.dataset_id}`; $('#attempt-label').textContent = `훈련 ID: ${attempt.attempt_id}`;
  $('#problem-description').textContent = pretty(attempt.problem?.description || ''); $('#problem-metadata').replaceChildren();
  for (const [key, value] of Object.entries(attempt.problem || {})) if (!['title', 'description', 'schema', 'problem_id', 'dictionary'].includes(key)) $('#problem-metadata').append(el('p', `${key}: ${pretty(value)}`));
  $('#schema').replaceChildren(el('pre', pretty(attempt.problem?.schema || attempt.problem?.dictionary || '데이터 사전을 불러오지 못했습니다.')));
  $('#hints').replaceChildren(); for (const hint of attempt.hints || []) $('#hints').append(el('p', hint.content || pretty(hint)));
  history.replaceState(null, '', `/?attempt=${encodeURIComponent(id)}`); renderSaved(); renderReports(); renderClaims(); tab('analysis'); notice();
}
function draftChanged() { state.sections = Object.fromEntries($$('[data-section]').map(node => [node.dataset.section, node.value])); state.generation++; state.dirty = true; $('#save-status').textContent = '저장 대기'; clearTimeout(state.saveTimer); state.saveTimer = setTimeout(() => flushDraft().catch(() => {}), 700); }
async function flushDraft() {
  clearTimeout(state.saveTimer);
  if (state.savePromise) { await state.savePromise; if (state.dirty) return flushDraft(); return; }
  if (!state.dirty || !state.attempt) return;
  if (state.conflict) throw new Error('다른 화면에서 초안을 수정했습니다. 최신 수정 번호를 확인한 뒤 내 입력 저장을 선택하세요.');
  state.savePromise = (async () => {
    while (state.dirty) {
      const generation = state.generation; const sections = { ...state.sections }; $('#save-status').textContent = '저장 중…';
      try { const draft = await api(attemptPath('/draft'), { method: 'PUT', body: JSON.stringify({ revision: state.revision, sections }) }); state.revision = draft.revision ?? draft.draft?.revision; if (!Number.isInteger(state.revision)) throw new Error('초안 수정 번호를 확인할 수 없습니다.'); state.dirty = generation !== state.generation; $('#draft-error').textContent = ''; $('#save-status').textContent = state.dirty ? '저장 대기' : '저장됨'; }
      catch (error) { $('#save-status').textContent = '저장 실패 · 입력 유지'; $('#draft-error').textContent = error.message; if (error.status === 409) { state.conflict = true; $('#resolve-conflict').hidden = false; } throw error; }
    }
  })();
  try { await state.savePromise; } finally { state.savePromise = null; }
}
function renderResult(container, execution) {
  const scope = `미리보기 ${execution.preview_row_count ?? execution.rows?.length ?? 0}행 / 전체 ${execution.total_row_count ?? '확인되지 않음'}행 · ${execution.result_complete ? '전체 결과 확보' : '전체 결과 미확보'}${execution.truncated ? ' · 미리보기 잘림' : ''}`;
  container.append(el('p', `상태: ${execution.status} · ${execution.duration_ms ?? '?'}ms`, 'muted'));
  if (execution.error) container.append(el('p', `${execution.error.code || ''}: ${execution.error.message || pretty(execution.error)}`, 'error'));
  if (execution.status === 'success') { container.append(el('p', scope, 'muted')); if (!(execution.rows || []).length) container.append(el('p', '조회가 성공했으며 결과는 0행입니다.', 'muted')); }
  if ((execution.columns || []).length) { const scroll = el('div', undefined, 'table-scroll'); const table = el('table'); const head = el('thead'), header = el('tr'); for (const col of execution.columns) header.append(el('th', `${col.name} (${col.type})`)); head.append(header); const body = el('tbody'); for (const row of execution.rows || []) { const tr = el('tr'); row.forEach(value => tr.append(el('td', value === null ? 'NULL' : typeof value === 'object' ? pretty(value) : value))); body.append(tr); } table.append(head, body); scroll.append(table); container.append(scroll); }
}
function renderExecution(item) {
  const container = el('div', undefined, 'execution'); container.append(el('h3', '이번 실행'), el('pre', item.sql)); renderResult(container, item.result); const button = el('button', '기록에 저장');
  const status = el('p', `미저장 · 서버 임시 보관 ${item.result.expires_in_seconds ?? '확인 필요'}초. 만료 후에는 다시 실행해야 합니다.`, 'muted');
  button.onclick = () => busy(button, async () => { if (item.saved) return; try { const saved = await post(attemptPath('/executions/save'), { execution_id: item.result.execution_id, request_id: item.requestId }); item.saved = true; (state.attempt.saved_executions ||= []).push(saved); button.textContent = '저장 완료'; status.textContent = '선택한 실행 당시 SQL과 결과를 저장했습니다.'; renderSaved(); renderClaims(); } catch (error) { status.textContent = `저장 실패: ${error.message} · 결과는 이 화면에 유지됩니다.`; throw error; } });
  container.append(button, status); $('#executions').prepend(container);
}
function savedResult(saved) { return saved.result || saved.execution || saved; }
function successfulSaved() { return (state.attempt.saved_executions || []).filter(saved => savedResult(saved).status === 'success'); }
function renderSaved() {
  $('#saved').replaceChildren(); $('#coach-evidence').replaceChildren(el('option', '근거 선택 안 함')); $('#coach-evidence').firstChild.value = '';
  for (const saved of state.attempt.saved_executions || []) { const container = el('details'); container.append(el('summary', `${saved.saved_execution_id} · ${savedResult(saved).status}`), el('pre', saved.sql || '')); renderResult(container, savedResult(saved)); $('#saved').append(container); }
  if (!(state.attempt.saved_executions || []).length) $('#saved').append(el('p', '저장한 실행이 없습니다.', 'muted'));
  for (const saved of successfulSaved()) { const option = el('option', `${saved.saved_execution_id} · ${(saved.sql || '').slice(0, 70)}`); option.value = saved.saved_execution_id; $('#coach-evidence').append(option); }
}
function claimTexts() { return ($('#discoveries').value || '').split(/\n\s*\n/).map(text => text.trim()).filter(Boolean); }
function renderClaims() {
  $('#claims').replaceChildren(); claimTexts().forEach((text, index) => {
    const claim = el('div', undefined, 'claim'); claim.append(el('h3', `발견 ${index + 1}의 근거`), el('p', text, 'prose')); const selected = state.claimEvidence.get(index) || new Set(); state.claimEvidence.set(index, selected);
    for (const saved of successfulSaved()) { const label = el('label', undefined, 'evidence-option'); const checkbox = el('input'); checkbox.type = 'checkbox'; checkbox.checked = selected.has(saved.saved_execution_id); checkbox.onchange = () => { if (checkbox.checked) selected.add(saved.saved_execution_id); else selected.delete(saved.saved_execution_id); state.reportRequest = null; }; label.append(checkbox, el('span', `${saved.saved_execution_id} · ${(saved.sql || '').slice(0, 100)}`)); claim.append(label); }
    if (!successfulSaved().length) claim.append(el('p', '연결 가능한 성공 실행이 없습니다. 분석 단계에서 실행 후 기록에 저장하세요.', 'muted')); $('#claims').append(claim);
  });
}
function renderReports() {
  $('#reports').replaceChildren();
  for (const report of [...(state.attempt.reports || [])].reverse()) {
    const node = el('div', undefined, 'report-entry'); node.append(el('h3', `보고서 v${report.report_version ?? '?'} · ${report.report_id}`), el('pre', pretty({ content: report.content, claims: report.claims })));
    const reviewButton = el('button', '이 제출본 리뷰 요청'); const editButton = el('button', '이 제출본을 수정'); let requestId = uid();
    reviewButton.onclick = () => busy(reviewButton, async () => { const review = await post(attemptPath(`/reports/${encodeURIComponent(report.report_id)}/review`), { request_id: requestId }); (state.attempt.reviews ||= []).push({ ...review, report_id: review.report_id || report.report_id }); requestId = uid(); renderReports(); if (review.status !== 'completed' && review.status !== 'success') notice(review.error?.message || '리뷰를 완료하지 못했습니다. 제출본은 보존되며 재시도할 수 있습니다.'); });
    editButton.onclick = () => { if (state.dirty && !confirm('현재 작성 중인 서술 입력을 이 제출본 내용으로 바꿀까요?')) return; state.previousReport = report.report_id; for (const key of ['problem_definition', 'hypothesis', 'limitations', 'next_actions']) $(`[data-section="${key}"]`).value = report.content?.[key] || ''; $('#discoveries').value = (report.claims || []).map(claim => claim.text).join('\n\n'); state.claimEvidence.clear(); (report.claims || []).forEach((claim, index) => state.claimEvidence.set(index, new Set((claim.evidence_refs || []).map(ref => ref.saved_execution_id)))); draftChanged(); renderClaims(); tab('report'); $('#report-status').textContent = `${report.report_id}의 수정본 작성 중 · 제출하면 새 버전이 됩니다.`; };
    node.append(reviewButton, document.createTextNode(' '), editButton); for (const review of state.attempt.reviews || []) if (review.report_id === report.report_id) { node.append(el('h3', `리뷰 · ${review.status}`), el('pre', pretty(review.feedback || review.error || review))); } $('#reports').append(node);
  }
  if (!(state.attempt.reports || []).length) $('#reports').append(el('p', '아직 제출한 보고서가 없습니다.', 'muted'));
}
$$('nav button').forEach(button => { button.onclick = () => { if (button.dataset.tab === 'report') renderClaims(); tab(button.dataset.tab); }; });
$$('[data-section]').forEach(node => { node.addEventListener('input', () => { draftChanged(); state.reportRequest = null; if (node.id === 'discoveries') renderClaims(); }); });
$('#save-draft').onclick = () => busy($('#save-draft'), flushDraft);
$('#resolve-conflict').onclick = () => busy($('#resolve-conflict'), async () => { const latest = await api(attemptPath()); state.revision = latest.draft.revision; state.conflict = false; await flushDraft(); $('#resolve-conflict').hidden = true; });
$('#execute').onclick = () => busy($('#execute'), async () => { const sql = $('#sql').value; if (!sql.trim()) throw new Error('실행할 SQL을 입력하세요.'); notice('SQL 실행 중…'); const result = await post(attemptPath('/execute'), { sql }); const item = { sql, result, requestId: uid(), saved: false }; state.executions.push(item); renderExecution(item); notice(); });
$('#submit-report').onclick = () => busy($('#submit-report'), async () => { await flushDraft(); const claims = claimTexts().map((text, index) => ({ claim_id: `claim-${index + 1}`, text, evidence_refs: [...(state.claimEvidence.get(index) || [])].map(saved_execution_id => ({ saved_execution_id })) })); const payload = { revision: state.revision, previous_report_id: state.previousReport, content: Object.fromEntries(['problem_definition', 'hypothesis', 'limitations', 'next_actions'].map(key => [key, state.sections[key] || ''])), claims }; const signature = JSON.stringify(payload); if (state.reportRequest?.signature !== signature) state.reportRequest = { signature, requestId: uid() }; const report = await post(attemptPath('/reports'), { ...payload, request_id: state.reportRequest.requestId }); if (!(state.attempt.reports || []).some(item => item.report_id === report.report_id)) (state.attempt.reports ||= []).push(report); $('#report-status').textContent = `제출 완료 · ${report.report_id}`; renderReports(); tab('history'); notice('보고서가 제출되었습니다. 해당 제출본으로 리뷰를 요청할 수 있습니다.'); });
$$('[data-hint]').forEach(button => { button.onclick = () => busy(button, async () => { const hint = await post(attemptPath('/hints'), { level: button.dataset.hint }); $('#hints').append(el('p', hint.content)); }); });
$('#explanation-button').onclick = () => busy($('#explanation-button'), async () => { const result = await post(attemptPath('/explanation'), {}); $('#explanation').textContent = `${result.sql || ''}\n\n${pretty(result.explanation || '')}`; state.attempt.explanation_viewed = true; });
$('#coach').onclick = () => busy($('#coach'), async () => { const message = $('#coach-message').value.trim(); if (!message) throw new Error('코칭 질문을 입력하세요.'); await flushDraft(); const result = await post(attemptPath('/coach'), { message, saved_execution_id: $('#coach-evidence').value || null }); $('#coach-result').textContent = pretty(result.feedback || result.error || result); });
$('#connect').onclick = () => busy($('#connect'), async () => { const result = await post('/api/auth/start', {}); if (!result.authorization_url) throw new Error(result.message || '인증 주소를 받지 못했습니다.'); const url = new URL(result.authorization_url); if (url.protocol !== 'https:') throw new Error('인증 주소는 HTTPS여야 합니다.'); window.open(url.href, '_blank', 'noopener,noreferrer'); notice('브라우저에서 공식 로그인과 권한 승인을 완료하세요. 돌아오면 연결 상태를 확인합니다.'); });
$('#disconnect').onclick = () => busy($('#disconnect'), async () => { await post('/api/auth/disconnect', {}); await authStatus(); });
$('#home-button').onclick = () => busy($('#home-button'), async () => { if (!(await canLeave())) return; await home(); clearTimeout(state.saveTimer); state.attempt = null; $('#workspace').hidden = true; $('#home').hidden = false; history.replaceState(null, '', '/'); });
window.addEventListener('beforeunload', event => { if (state.dirty || (state.attempt && ($('#sql').value.trim() || state.executions.some(item => !item.saved)))) { event.preventDefault(); event.returnValue = ''; } });
window.addEventListener('focus', authStatus);
(async () => { await authStatus(); try { await home(); const id = new URLSearchParams(location.search).get('attempt'); if (id) await openAttempt(id); } catch (error) { notice(error.message); } })();

