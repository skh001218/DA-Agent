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
function view(name) { $('#metrics-panel').hidden = name !== 'metrics'; $('#capabilities-panel').hidden = name !== 'capabilities'; $('#metrics-tab').classList.toggle('active',name === 'metrics'); $('#capabilities-tab').classList.toggle('active',name === 'capabilities'); $('#quality-panel').hidden = name !== 'quality'; $('#pilot-panel').hidden = name !== 'pilot'; $('#quality-tab').classList.toggle('active', name === 'quality'); $('#pilot-tab').classList.toggle('active', name === 'pilot'); }
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
  if (run.status === 'running') state.polling = setTimeout(async () => { try { if (!state.dirty) await openRun(id); else { await listRuns(); notice('평가는 실행 중입니다. 판정 입력을 보호하기 위해 상세 갱신을 멈췄습니다. 저장 후 진행 상태를 확인하세요.'); } } catch (error) { notice(error.message); } }, 4000);
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

const assessmentResults = {
 task_pair:[['meaningful_difference','의미 있는 차이'],['no_difference','차이 없음'],['intentional_repeat','의도한 반복']],
 difficulty:[['appropriate','적절'],['ambiguous','모호'],['unanalyzable','분석 불가'],['too_easy','너무 쉬움'],['too_hard','너무 어려움']],
 coaching:[['helpful','유용'],['disruptive','방해'],['repeated_question','반복 질문']],
 review:[['pass','충족'],['misdiagnosis','오진'],['missed_error','오류 누락'],['alternative_rejected','타당한 대안 거부'],['answer_exposure','정답 노출']],
 pilot:[['independent_completed','독립 완료'],['assisted','도움 받음'],['interrupted','중단'],['ongoing','진행 중'],['unknown','확인 불가']],
 report_pair:[['needs_improvement','보완 필요'],['improved','보완 완료'],['not_improved','보완되지 않음']]
};
function resultOptions(kind) { return [['pending','미판정'],...(assessmentResults[kind] || [])]; }
function metricQuery() { const query=new URLSearchParams(); for (const [key,value] of new FormData($('#metric-filters'))) if (value) query.set(key,value); return query; }
async function loadMetrics() {
 const data = await api('/api/quality/metrics?'+metricQuery());
 $('#metric-cards').replaceChildren();
 const metricTitles={generation_success:'출제 성공률',diversity:'과제 다양성',difficulty_fit:'요청 난이도 적합성',coaching_appropriateness:'코칭 적절성',evaluation_reliability:'평가 신뢰성',independent_performance:'독립 수행',revision_effect:'수정 효과',wait_usage:'대기 시간·사용량'};
 for (const [key,title] of Object.entries(metricTitles)) { const m=data[key] || {};const card=node('article');
 card.append(node('h3',title),node('p',key==='wait_usage'?`중앙 대기 ${m.duration?.median_ms === null || m.duration?.median_ms === undefined ? '미측정' : m.duration.median_ms+'ms'}`:m.rate===null || m.rate===undefined?'미측정':`${(m.rate*100).toFixed(1)}%`),node('p',`출처: ${['generation_success','evaluation_reliability','wait_usage'].includes(key)?'서버 이벤트':key==='independent_performance'?'학습자 응답·사람 관찰':'사람 판정'}`, 'muted'),node('p',`분자 ${m.numerator ?? '미측정'} · 분모 ${m.denominator ?? m.duration?.count ?? '미측정'} · 측정 상태 ${m.measurement === 'measured' ? '측정됨' : m.measurement === 'unmeasured' ? '미측정' : '소요 시간·사용량'} · 결측 ${m.missing ?? m.duration?.missing ?? '상세 확인'}`,'muted'),node('pre',JSON.stringify(m,null,2)));$('#metric-cards').append(card); }
 $('#metric-integrity').textContent=`집계 상태: ${data.collection?.incomplete ? '불완전 · 로그 수집 또는 보관 범위 확인 필요' : '수집 상태 정상'}\n보관 ${data.collection?.retention_days ?? '미확인'}일 · 보관 범위 밖 ${data.collection?.outside_retention ? '포함됨' : '포함되지 않음'}\n실패·취소·진행 중: ${JSON.stringify(data.wait_usage?.statuses || {})}`;
 $('#metric-definitions').textContent=JSON.stringify({metrics_version:data.metrics_version,versions:data.versions,filters:Object.fromEntries(metricQuery()),collection:data.collection,definitions:{generation_success:'ready 요청 / 실제 준비를 시도한 대상 요청 (서버 이벤트). 상태별 실패·취소·진행 중은 카드 상세 참고',diversity:'의미 있는 차이 / 판정된 과제 쌍',difficulty_fit:'적절 / 판정된 요청',coaching_appropriateness:'유용 / 판정된 코칭',evaluation_reliability:'리뷰 응답 완료 / 종료된 리뷰 요청 (서버 이벤트). 오진·누락 등 사람 판정과 반복 표본 점수 범위는 별도 상세' ,independent_performance:'도움 없이 완료한 파일럿 / 선택된 파일럿 기록 (학습자 응답·사람 관찰)' ,revision_effect:'최초 보완 필요 판정과 수정 보완 완료 판정의 연결 대상 / 최초 보완 필요 대상 (사람 판정)' ,wait_usage:'서버 요청 소요 시간·실패·취소·사용량'}},null,2);
}
async function downloadMetric(format) {
 const query=metricQuery(); query.set('format',format); const response=await fetch('/api/quality/metrics/export?'+query);
 if(!response.ok) throw new Error('지표 내보내기 실패 · 현재 필터와 입력은 유지됩니다.');
 const blob=await response.blob(); const content=await blob.text(); if (!content.trim()) throw new Error('빈 내보내기 파일입니다.');
 const url=URL.createObjectURL(blob); const a=node('a'); a.href=url;a.download=`quality-metrics.${format}`;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);notice(`현재 필터로 ${format.toUpperCase()} 파일을 요청했습니다.`);
}
async function loadAssessments() {
 const data=await api('/api/quality/assessments'); $('#assessments').replaceChildren();
 for(const item of data.assessments || []) {
  const group=node('article'); group.append(node('h3',`${item.target_kind} · ${item.target_id}`),node('p',`대상 버전 ${item.target_version} · 표본 ${item.sample_id || '없음'} · 회차 ${item.repetition || '없음'} · 수정 번호 ${item.revision} · 출처 ${item.source}`,'muted'));
  const result=field(group,'판정',select(resultOptions(item.target_kind),item.result)); const reviewer=field(group,'검토자',node('input'));reviewer.value=item.reviewer;
  const note=field(group,'근거',node('textarea'));note.value=item.note || '';
  const revisions=node('details');revisions.append(node('summary','이전 판정과 변경 이력'),node('pre',JSON.stringify((data.history || []).filter(x=>x.assessment_id===item.assessment_id),null,2)));group.append(revisions);
  const save=node('button','판정 수정 저장'); save.onclick=()=>busy(save,async()=>{try{const updated=await api(`/api/quality/assessments/${encodeURIComponent(item.assessment_id)}`,'PATCH',{expected_revision:item.revision,result:result.value,reviewer:reviewer.value,note:note.value}); item.revision=updated.revision; notice(`수정 번호 ${item.revision}으로 판정을 저장했습니다.`);}catch(error){notice(`판정 저장 실패 · 입력 유지: ${error.message} · 최신 판정 목록에서 변경 이력을 확인하세요.`);}});const latest=node('button','최신 판정과 수정 번호 확인');latest.onclick=()=>busy(latest,async()=>{const fresh=await api('/api/quality/assessments');const current=fresh.assessments.find(x=>x.assessment_id===item.assessment_id);if(!current)throw new Error('판정 기록이 없어졌습니다.');group.append(node('pre',JSON.stringify(current,null,2)));if(confirm(`최신 판정 수정 번호 ${current.revision}을 확인했습니다. 내 입력을 유지하고 이 수정 번호로 다시 저장할까요?`))item.revision=current.revision;});group.append(save,latest);$('#assessments').append(group);
 }
 if(!(data.assessments || []).length) $('#assessments').append(node('p','아직 기록된 판정이 없습니다.','muted'));
}
$('#metrics-tab').onclick=()=>{view('metrics');loadMetrics().catch(e=>notice(e.message));loadAssessments().catch(e=>notice(e.message));};
$('#refresh-metrics').onclick=()=>busy($('#refresh-metrics'),loadMetrics);
$('#download-metrics-json').onclick=()=>busy($('#download-metrics-json'),()=>downloadMetric('json'));
$('#download-metrics-csv').onclick=()=>busy($('#download-metrics-csv'),()=>downloadMetric('csv'));
$('#assessment-kind').onchange=()=>{ const replacement=select(resultOptions($('#assessment-kind').value));replacement.id='assessment-verdict';$('#assessment-verdict').replaceWith(replacement);};$('#assessment-kind').onchange();
$('#save-assessment').onclick=()=>busy($('#save-assessment'),async()=>{
 const body={target_kind:$('#assessment-kind').value,target_id:$('#assessment-target').value.trim(),target_version:$('#assessment-version').value.trim(),reviewer:$('#assessment-reviewer').value.trim(),source:$('#assessment-source').value,criteria_version:'quality-metrics-v2',result:$('#assessment-verdict').value,note:$('#assessment-note').value};
 for(const [key,id] of [['sample_id','assessment-sample'],['paired_target_id','assessment-paired-target'],['paired_target_version','assessment-paired-version']]) if($('#'+id).value.trim()) body[key]=$('#'+id).value.trim();
 if(body.sample_id && $('#assessment-repetition').value)body.repetition=Number($('#assessment-repetition').value);
 if(body.sample_id && !body.repetition)throw new Error('표본 식별자를 입력했다면 회차도 입력하세요.');
 await api('/api/quality/assessments','POST',body);$('#assessment-status').textContent='人 판정을 저장했습니다. 대상과 입력을 유지했습니다.'.replace('人','사람');await loadAssessments();
});
$('#refresh-assessments').onclick=()=>busy($('#refresh-assessments'),loadAssessments);
let operatorCapabilities=[];let operatorSamples=[];
async function loadCapabilities() {
 const data=await api('/api/training/capabilities');operatorCapabilities=data.capabilities || [];$('#operator-capability').replaceChildren();
 for(const c of operatorCapabilities){const o=node('option',`${c.title} · ${c.status}`);o.value=c.capability_id;$('#operator-capability').append(o);}
 $('#rule-status').textContent='서버 규칙 상태를 확인했습니다. 표본을 열어 검토하세요.';
}
function rulePath(suffix) {return `/api/training/capabilities/${encodeURIComponent($('#operator-capability').value)}/${suffix}`;}
async function loadRuleSamples() {
 const data=await api(rulePath('samples'));operatorSamples=data.samples || [];$('#rule-samples').replaceChildren();$('#rule-reviewed').checked=false;
 for(const sample of operatorSamples){const d=node('details');const checkbox=node('input');checkbox.type='checkbox';checkbox.value=sample.sample_id;checkbox.className='rule-sample-selection';checkbox.disabled=sample.status!=='validated';const label=node('label');label.append(checkbox,document.createTextNode(`승인 표본 선택 · ${sample.sample_id}`));d.append(node('summary',`${sample.difficulty || ''} · ${sample.status}`),label,node('pre',JSON.stringify(sample.public || sample,null,2)));$('#rule-samples').append(d);}
 $('#rule-status').textContent='공개 표본과 검증 상태를 읽고 승인에 사용할 표본을 직접 선택하세요.';
}
$('#capabilities-tab').onclick=()=>{view('capabilities');loadCapabilities().catch(e=>notice(e.message));};
$('#refresh-capabilities').onclick=()=>busy($('#refresh-capabilities'),loadCapabilities);
$('#load-rule-samples').onclick=()=>busy($('#load-rule-samples'),loadRuleSamples);
$('#generate-rule-samples').onclick=()=>busy($('#generate-rule-samples'),async()=>{ const c=operatorCapabilities.find(x=>x.capability_id===$('#operator-capability').value);if(!c)throw new Error('먼저 규칙을 선택하세요.');for(const difficulty of ['beginner','intermediate','advanced'])await api(rulePath('samples'),'POST',{difficulty,task_kind:c.task_kind,user_count:200});await loadRuleSamples(); });
async function approveRule(result) {
 const reviewer=$('#rule-reviewer').value.trim();if(!reviewer)throw new Error('검토자를 입력하세요.');
 const sample_ids=[...document.querySelectorAll('.rule-sample-selection:checked')].map(x=>x.value);
 if(result==='approved' && (!$('#rule-reviewed').checked || sample_ids.length<3))throw new Error('3개 이상 표본을 선택하고 실제 검토 확인을 체크하세요.');
 await api(rulePath('approval'),'POST',{reviewer,sample_ids,rules_version:'access-rules-v2',result});$('#rule-status').textContent=result==='approved'?'규칙 승인 저장 완료':'규칙 비활성화 완료';await loadCapabilities();
}
$('#approve-rule').onclick=()=>busy($('#approve-rule'),()=>approveRule('approved'));
$('#disable-rule').onclick=()=>busy($('#disable-rule'),()=>approveRule('disabled'));
