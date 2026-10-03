'use strict';
const $ = selector => document.querySelector(selector);
const node = (tag, text, cls) => { const n = document.createElement(tag); if (text !== undefined) n.textContent = String(text); if (cls) n.className = cls; return n; };
const labels = { problem_definition: '문제 정의', analysis_approach: '분석 접근', sql_accuracy: 'SQL 정확성', interpretation: '결과 해석', next_actions: '추가 분석·액션' };
const state = { run: null, request: null, packages: [], polling: null, dirty: false };
const statusLabels = { running: '실행 중', completed: '호출 종료', interrupted: '중단', pending: '검토 대기', pass: '통과', fail: '미달' };
const kst = value => value ? new Intl.DateTimeFormat('ko-KR', {timeZone:'Asia/Seoul',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}).format(new Date(value)) + ' KST' : '미기록';
function notice(text = '') { $('#notice').textContent = text; $('#notice').hidden = !text; }
async function api(path, method = 'GET', value) {
  const response = await fetch(path, { method, headers: { 'Content-Type': 'application/json' }, ...(value === undefined ? {} : { body: JSON.stringify(value) }) });
  let body; try { body = await response.json(); } catch { throw new Error('서버 응답을 읽지 못했습니다. 입력을 유지하고 다시 시도하세요.'); }
  if (!response.ok) throw new Error(body.detail?.message || '요청을 처리하지 못했습니다.'); return body;
}
async function busy(button, fn) { if (button.disabled) return; button.disabled = true; try { await fn(); } catch (error) { notice(error.message); } finally { button.disabled = false; } }
function select(options, selected = 'pending') { const s = node('select'); for (const [value, title] of options) { const opt = node('option', title); opt.value = value; opt.selected = value === selected; s.append(opt); } return s; }
function field(container, title, element) { const label = node('label', title); label.append(element); container.append(label); return element; }
function badge(value) { return node('span', statusLabels[value] || value, 'result-badge ' + value); }
async function download(path, name) { await api(path); const a = node('a'); a.href = path; a.download = name; document.body.append(a); a.click(); a.remove(); notice('내보내기 파일을 요청했습니다. 브라우저의 다운로드 목록을 확인하세요.'); }
function dirtyTracker(container) { container.addEventListener('input', () => { state.dirty = true; }); container.addEventListener('change', () => { state.dirty = true; }); }
function canReplace() { if (state.dirty) { notice('검토 입력이 아직 저장되지 않았습니다. 먼저 해당 기록의 저장 버튼을 눌러주세요.'); return false; } return true; }
function view(name) { $('#quality-panel').hidden = name !== 'quality'; $('#pilot-panel').hidden = name !== 'pilot'; $('#quality-tab').classList.toggle('active', name === 'quality'); $('#pilot-tab').classList.toggle('active', name === 'pilot'); }
async function listRuns() {
  const data = await api('/api/quality/runs'); $('#runs').replaceChildren();
  for (const run of data.runs) { const card = node('div', undefined, 'run-card'); const button = node('button', `${run.release_version} · ${kst(run.started_at)}`, state.run === run.run_id ? 'selected' : ''); button.onclick = () => busy(button, async () => { if (canReplace()) await openRun(run.run_id); }); card.append(button, node('p', `${statusLabels[run.status]} · ${run.completed_calls}/15회`), badge(run.verdict)); $('#runs').append(card); }
  if (!data.runs.length) $('#runs').append(node('p', '아직 실행한 검증이 없습니다.', 'muted'));
}
function renderFeedback(container, result) {
  if (result.status !== 'completed') { container.append(node('p', result.error?.message || '평가 응답을 완료하지 못했습니다.', 'error')); return; }
  const feedback = result.feedback; container.append(node('h3', `${feedback.total_score} / 100점`));
  for (const criterion of feedback.criteria) container.append(node('p', `${labels[criterion.key]}: ${criterion.score}/${criterion.weight}`, 'muted'), node('p', criterion.reason, 'prose'));
  for (const [key, title] of [['strengths', '잘한 점'], ['improvements', '보완할 점'], ['next_steps', '다음 행동']]) { container.append(node('h3', title)); for (const text of feedback[key]) container.append(node('p', text, 'prose')); }
}
async function openRun(id) {
  const run = await api(`/api/quality/runs/${encodeURIComponent(id)}`); state.run = id;
  if (state.dirty) { await listRuns(); notice('입력 중인 판정을 보호하기 위해 상세 갱신을 멈췄습니다. 저장 후 새로고침하세요.'); return; }
  const target = $('#run-detail'); target.replaceChildren(node('h2', '평가 결과 비교'), badge(run.verdict), node('p', `${statusLabels[run.status]} · ${run.completed_calls}/15회`), node('p', `${run.package_id} / ${run.release_version} · ${run.dataset_id}`, 'muted'), node('p', `표본 ${run.fixture_version} · 규칙 ${run.rules_version} · 설정 모델 ${run.configured_model || '응답별 기록 확인'}`, 'muted'));
  if (run.error) target.append(node('p', run.error, 'error'));
  const completed = run.samples.flatMap(s => s.results);
  target.append(node('p', `유효 리뷰 ${completed.filter(r => r.status === 'completed').length}회 · 실패 ${completed.filter(r => r.status === 'failed').length}회`, 'muted'));
  const exportButton = node('button', '검증 결과 JSON 내보내기'); exportButton.onclick = () => busy(exportButton, async () => { if (!canReplace()) return; await download(`/api/quality/runs/${id}/export`, `quality-${run.run_id}.json`); }); target.append(exportButton);
  target.append(node('h3', '한눈에 비교'));
  const overview = node('div', undefined, 'table-scroll'); const table = node('table'); const head = node('thead'); const header = node('tr');
  for (const title of ['표본', '1회', '2회', '3회', '점수 범위', '판정']) header.append(node('th', title)); head.append(header); table.append(head);
  const body = node('tbody'); for (const sample of run.samples) { const row = node('tr'); const name = node('td'); const link = node('a', sample.title); link.href = `#sample-${sample.id}`; name.append(link); row.append(name); for (let n = 1; n <= 3; n++) { const result = sample.results.find(r => r.repetition === n); row.append(node('td', !result ? '대기' : result.status === 'completed' ? result.feedback.total_score : '실패')); } row.append(node('td', sample.score_range === null ? '—' : `${sample.score_range}점${sample.score_range > 10 ? ' · 검토 필요' : ''}`)); const verdict = node('td'); verdict.append(badge(sample.verdict)); row.append(verdict); body.append(row); } table.append(body); overview.append(table); target.append(overview);
  for (const sample of run.samples) {
    const section = node('section', undefined, 'quality-sample'); section.append(node('h2', sample.title), badge(sample.verdict), node('p', `점수 범위: ${sample.score_range === null ? '산정 전' : sample.score_range + '점'}${sample.score_range > 10 ? ' · 10점 초과, 검토 필요' : ''}`));
    section.id = `sample-${sample.id}`;
    if (sample.expected_levels) section.append(node('p', '항목별 수준 제안 (확정 판정 아님): ' + Object.entries(sample.expected_levels).map(([key, range]) => `${labels[key]} ${range[0]}~${range[1]}`).join(' · '), 'muted'));
    const reference = node('details'); reference.append(node('summary', '표본 보고서·실행 근거 보기'), node('pre', JSON.stringify(sample.report, null, 2)), node('pre', sample.sql), node('pre', JSON.stringify(sample.evidence[0].result, null, 2))); section.append(reference);
    const grid = node('div', undefined, 'review-comparison');
    for (let repetition = 1; repetition <= 3; repetition++) {
      const result = sample.results.find(r => r.repetition === repetition); const column = node('div'); column.append(node('h3', `${repetition}회차`));
      if (!result) { column.append(node('p', '아직 실행되지 않았습니다.', 'muted')); grid.append(column); continue; }
      column.append(node('p', `${result.model || '모델 정보 없음'} · ${kst(result.finished_at)}`, 'muted')); renderFeedback(column, result);
      if (result.status === 'completed') {
        column.append(node('h3', '사람 검토')); const checks = sample.checks.map((check, index) => field(column, check, select([['pending', '미판정'], ['pass', '충족'], ['fail', '미충족']], result.human?.checks[index])));
        const critical = field(column, '중대한 오평가가 있나요?', select([['pending', '미판정'], ['no', '없음'], ['yes', '있음']], result.human?.critical_error));
        const reviewer = field(column, '검토자', node('input')); reviewer.value = result.human?.reviewer || '';
        const note = field(column, '판정 근거·검토 메모', node('textarea')); note.value = result.human?.note || '';
        const save = node('button', '사람 판정 저장'); save.onclick = () => busy(save, async () => { await api(`/api/quality/runs/${id}/human`, 'PUT', { sample_id: sample.id, repetition, checks: checks.map(s => s.value), critical_error: critical.value, reviewer: reviewer.value, note: note.value }); column.dataset.dirty = ''; state.dirty = Boolean(document.querySelector('[data-dirty="yes"]')); notice('사람 판정을 저장했습니다. 다른 입력을 모두 저장한 뒤 새로고침하면 통과 상태가 갱신됩니다.'); await listRuns(); }); column.append(save);
        column.addEventListener('input', () => { column.dataset.dirty = 'yes'; }); column.addEventListener('change', () => { column.dataset.dirty = 'yes'; }); dirtyTracker(column);
      }
      grid.append(column);
    }
    section.append(grid); target.append(section);
  }
  const params = new URLSearchParams(location.search); params.set('run', id); history.replaceState(null, '', `${location.pathname}?${params}`);
  await listRuns(); clearTimeout(state.polling);
  if (run.status === 'running') state.polling = setTimeout(async () => { try { if (!state.dirty) await openRun(id); else { await listRuns(); notice('評価は実行中です。入力中の判定を保護するため、詳細の自動更新を止めています。保存後に進行状態を更新してください。'); } } catch (error) { notice(error.message); } }, 4000);
}
function reportView(container, report, attempt) {
  if (!report) { container.append(node('p', '提出なし', 'muted')); return; }
  container.append(node('h3', `보고서 v${report.report_version}`), node('p', kst(report.submitted_at), 'muted'));
  for (const [key, text] of Object.entries(report.content)) if (text) container.append(node('h3', labels[key] || ({ hypothesis: '가설·접근', limitations: '한계' })[key] || key), node('p', text, 'prose'));
  for (const claim of report.claims) { container.append(node('p', claim.text, 'prose')); for (const ref of claim.evidence_refs) { const saved = attempt.saved_executions.find(e => e.saved_execution_id === ref.saved_execution_id); if (saved) { const detail = node('details'); detail.append(node('summary', '연결된 저장 근거'), node('pre', saved.sql), node('pre', JSON.stringify(saved.result, null, 2))); container.append(detail); } } }
  for (const review of attempt.reviews.filter(r => r.report_id === report.report_id)) renderFeedback(container, review);
}
async function loadPilots() {
  const [data, attempts] = await Promise.all([api('/api/quality/pilots'), api('/api/attempts')]);
  if (state.dirty) { notice('입력 중인 파일럿 기록을 먼저 저장하세요.'); return; }
  $('#pilot-attempt').replaceChildren();
  for (const attempt of attempts.attempts) { const option = node('option', `${attempt.attempt_id.slice(0, 8)} · ${attempt.release_version} · ${kst(attempt.started_at)}`); option.value = attempt.attempt_id; $('#pilot-attempt').append(option); }
  $('#link-pilot').disabled = !attempts.attempts.length;
  $('#pilot-summary').replaceChildren(); const s = data.summary;
  for (const [title, value] of [['연결 참가자', `${s.participants}명 / 목표 5명`], ['도움 없이 리뷰 완료', `${s.independent_completions}명 / 목표 4명`], ['같은 독립 완료자의 구체적 다음 행동', `${s.actionable_independent}명 / 목표 4명`], ['핵심 조건 보완', `${s.improved}명 · 판정 대기 ${s.pending_judgments}명`]]) { const card = node('article'); card.append(node('h3', title), node('p', value)); $('#pilot-summary').append(card); }
  $('#pilots').replaceChildren();
  for (const pilot of data.pilots) {
    const entry = node('section', undefined, 'pilot-entry'); entry.append(node('h2', `${pilot.participant_code} · 훈련 ${pilot.attempt_id.slice(0, 8)}`), node('p', `${pilot.completed ? '리뷰 완료' : '리뷰 미완료'} · 힌트 ${pilot.hint_count}회 · 해설 ${pilot.attempt.explanation_viewed ? '참조' : '미참조'}`), node('p', `시작 ${kst(pilot.attempt.started_at)} · 기록 연결 ${kst(pilot.linked_at)}`, 'muted'));
    const assistance = field(entry, '개발자 도움을 받았나요?', select([['pending', '확인 대기'], ['no', '아니요'], ['yes', '예']], pilot.assistance));
    const assistanceNote = field(entry, '도움 내용', node('textarea')); assistanceNote.value = pilot.assistance_note;
    const stopped = field(entry, '중단 지점·이유', node('textarea')); stopped.value = pilot.stopped_at;
    const action = field(entry, '학습자가 설명한 다음 수정 행동', node('textarea')); action.value = pilot.next_action;
    const actionable = field(entry, '다음 행동이 구체적인가요? (검토자 판정)', select([['pending', '미판정'], ['yes', '예'], ['no', '아니요']], pilot.actionable));
    const improved = field(entry, '수정 보고서에 누락된 핵심 조건을 보완했나요?', select([['pending', '미판정'], ['yes', '예'], ['no', '아니요'], ['not_applicable', '최초 누락 없음 / 해당 없음']], pilot.improved));
    const observer = field(entry, '관찰·판정 근거', node('textarea')); observer.value = pilot.observer_note;
    const save = node('button', '파일럿 기록 저장', 'primary'); save.onclick = () => busy(save, async () => { await api(`/api/quality/pilots/${pilot.attempt_id}`, 'PUT', { participant_code: pilot.participant_code, assistance: assistance.value, assistance_note: assistanceNote.value, stopped_at: stopped.value, next_action: action.value, actionable: actionable.value, improved: improved.value, observer_note: observer.value }); entry.dataset.dirty = ''; state.dirty = Boolean(document.querySelector('[data-dirty="yes"]')); notice('파일럿 기록을 저장했습니다.'); if (!state.dirty) await loadPilots(); });
    const exportButton = node('button', '참가자 기록 JSON 내보내기'); exportButton.onclick = () => busy(exportButton, async () => { if (!canReplace()) return; await download(`/api/quality/pilots/${pilot.attempt_id}/export`, `pilot-${pilot.participant_code}.json`); });
    entry.append(save, document.createTextNode(' '), exportButton);
    const compare = node('details'); compare.append(node('summary', '최초·수정 보고서와 리뷰 비교')); const grid = node('div', undefined, 'report-compare'); const reports = pilot.attempt.reports; for (const [title, report] of [['최초 제출', reports[0]], ['최신 수정 제출', reports.length > 1 ? reports.at(-1) : null]]) { const column = node('div'); column.append(node('h3', title)); reportView(column, report, pilot.attempt); grid.append(column); } compare.append(grid); entry.append(compare);
    const events = node('details'); events.append(node('summary', '수집된 진행·오류 기록'), node('pre', JSON.stringify(pilot.events, null, 2))); entry.append(events);
    entry.addEventListener('input', () => { entry.dataset.dirty = 'yes'; }); entry.addEventListener('change', () => { entry.dataset.dirty = 'yes'; }); dirtyTracker(entry); $('#pilots').append(entry);
  }
  if (!data.pilots.length) $('#pilots').append(node('p', '연결된 참가자가 없습니다. 실제 참가자 테스트를 완료한 것으로 표시하지 않습니다.', 'muted'));
}
$('#start-quality').onclick = () => busy($('#start-quality'), async () => { if (!canReplace()) return; const pack = state.packages[Number($('#quality-package').value)]; if (!pack) throw new Error('문제 1 패키지를 선택하세요.'); state.request ||= crypto.randomUUID(); notice('표본 SQL을 검증하고 반복 평가를 시작합니다…'); const run = await api('/api/quality/runs', 'POST', { ...pack, request_id: state.request }); state.request = null; notice('평가를 시작했습니다. 새로고침해도 저장된 결과를 다시 열 수 있습니다.'); await openRun(run.run_id); });
$('#quality-package').onchange = () => { state.request = null; };
$('#refresh-quality').onclick = () => busy($('#refresh-quality'), async () => { if (!canReplace()) return; if (state.run) await openRun(state.run); else await listRuns(); });
$('#refresh-pilots').onclick = () => busy($('#refresh-pilots'), async () => { if (canReplace()) await loadPilots(); });
$('#link-pilot').onclick = () => busy($('#link-pilot'), async () => { if (!canReplace()) return; const id = $('#pilot-attempt').value; if (!id) throw new Error('먼저 훈련 시도를 시작하세요.'); const existing = (await api('/api/quality/pilots')).pilots.find(p => p.attempt_id === id); const body = existing ? Object.fromEntries(['assistance', 'assistance_note', 'stopped_at', 'next_action', 'actionable', 'improved', 'observer_note'].map(k => [k, existing[k]])) : {}; await api(`/api/quality/pilots/${id}`, 'PUT', { ...body, participant_code: $('#pilot-code').value.trim() }); notice('참가자를 연결했습니다. 실제 학습자의 응답과 관찰 내용을 기록하세요.'); await loadPilots(); });
$('#quality-tab').onclick = () => view('quality'); $('#pilot-tab').onclick = () => view('pilot');
window.addEventListener('beforeunload', event => { if (state.dirty) { event.preventDefault(); event.returnValue = ''; } });
(async () => { try { const [packages, cases] = await Promise.all([api('/api/packages'), api('/api/quality/cases')]); for (const pack of packages.packages) if (pack.problems.some(p => p.problem_id === 'problem-001')) state.packages.push({ package_id: pack.package_id, release_version: pack.release_version, problem_id: 'problem-001' }); state.packages.forEach((pack, i) => { const option = node('option', `${pack.package_id} · ${pack.release_version}`); option.value = i; $('#quality-package').append(option); }); $('#start-quality').disabled = !state.packages.length; for (const sample of cases.cases) $('#cases').append(node('span', sample.title)); await listRuns(); await loadPilots(); const run = new URLSearchParams(location.search).get('run'); if (run) await openRun(run); } catch (error) { notice(error.message); } })();
