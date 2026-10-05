// Real browser, current static files, read-only live APIs, simulated preparation responses.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs = require('node:fs/promises');
const path = require('node:path');
const assert = require('node:assert/strict');

async function main() {
  const browser = await chromium.launch({ headless: true, channel: 'msedge' });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const output = path.resolve('tests/artifacts/simple-training-browser-2026-10-05');
  await fs.mkdir(output, { recursive: true });
  const errors = [], payloads = [], apiPaths = [];
  page.on('pageerror', error => errors.push(error.message));
  let status = 'needs_clarification', rejectPost = false, emptyAttempts = false;
  const base = 'http://127.0.0.1:18088';
  await page.route(`${base}/**`, async route => {
    const request = route.request(), url = new URL(request.url());
    if (url.pathname.startsWith('/api/')) {
      apiPaths.push(url.pathname);
      if (url.pathname === '/api/attempts' && emptyAttempts) return route.fulfill({ json: { attempts: [] } });
      if (url.pathname.startsWith('/api/training/requests')) {
        if (request.method() === 'POST') {
          if (url.pathname === '/api/training/requests') {
            payloads.push(request.postDataJSON());
            if (rejectPost) return route.fulfill({ status: 503, json: { detail: '검증용 준비 실패' } });
          }
          if (url.pathname.endsWith('/cancel')) status = 'cancelled';
          if (url.pathname.endsWith('/clarify')) status = 'failed';
          if (url.pathname.endsWith('/retry')) status = 'needs_clarification';
        }
        return route.fulfill({ json: { request_id: 'ui-smoke', status, revision: 1,
          message: payloads.at(-1)?.message, retry_allowed: status === 'failed',
          questions: status === 'needs_clarification' ? ['관측 기간을 알려주세요.'] : [],
          states: [{ status }], error: status === 'failed' ? '검증용 준비 실패' : undefined } });
      }
      assert.equal(request.method(), 'GET', 'Live API access must remain read-only');
      return route.fulfill({ response: await page.request.get(`http://127.0.0.1:8087${url.pathname}${url.search}`) });
    }
    const file = url.pathname === '/' ? 'index.html' : path.basename(url.pathname);
    const contentType = file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html';
    return route.fulfill({ body: await fs.readFile(path.resolve('src/da_agent/static', file)), contentType });
  });
  const load = async () => {
    await page.goto(base);
    await page.waitForFunction(() => typeof document.querySelector('#request-training').onclick === 'function');
  };
  try {
    await load();
    assert.equal(await page.locator('#new-training-panel').isVisible(), true);
    assert.equal(await page.locator('#attempts').isVisible(), false);
    await page.locator('#training-request').fill('탭 전환 시 유지할 요청');
    await page.getByRole('tab', { name: '이어 하기', exact: true }).click();
    assert.equal(await page.locator('#resume-training-panel').isVisible(), true);
    assert.equal(await page.locator('#new-training-panel').isVisible(), false);
    assert.ok(new URL(page.url()).searchParams.get('home') === 'resume');
    await page.getByRole('tab', { name: '새 훈련', exact: true }).click();
    assert.equal(await page.locator('#training-request').inputValue(), '탭 전환 시 유지할 요청');
    await page.getByRole('tab', { name: '새 훈련', exact: true }).focus();
    await page.keyboard.press('ArrowRight');
    assert.equal(await page.getByRole('tab', { name: '이어 하기', exact: true }).getAttribute('aria-selected'), 'true');
    await page.reload();
    await page.waitForFunction(() => typeof document.querySelector('#request-training').onclick === 'function');
    assert.equal(await page.locator('#resume-training-panel').isVisible(), true);
    await page.screenshot({ path: path.join(output, 'resume-1280.png'), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await page.screenshot({ path: path.join(output, 'resume-390.png'), fullPage: true });
    await page.setViewportSize({ width: 1280, height: 900 });
    const resumeButton = page.locator('#attempts').getByRole('button', { name: '이어서 분석', exact: true }).first();
    await resumeButton.waitFor();
    await resumeButton.click();
    await page.locator('#workspace').waitFor({ state: 'visible' });
    await page.getByRole('button', { name: '3. 보고서 작성', exact: true }).click();
    assert.equal(await page.locator('#report').isVisible(), true);
    await page.getByRole('button', { name: '훈련 목록', exact: true }).click();
    await page.locator('#resume-training-panel').waitFor({ state: 'visible' });
    assert.equal(await page.locator('#attempts').isVisible(), true);
    emptyAttempts = true;
    await load();
    await page.getByRole('tab', { name: '이어 하기', exact: true }).click();
    await page.locator('#attempts').filter({ hasText: '아직 시작한 훈련이 없습니다.' }).waitFor();
    emptyAttempts = false;
    await load();
    for (const id of ['request-kind', 'request-domain', 'request-data', 'request-goal', 'request-time',
      'intentional-repeat', 'capability-status', 'recommendation', 'start-recommendation', 'packages']) {
      assert.equal(await page.locator(`#${id}`).count(), 0, id);
    }
    await page.locator('#request-training').click();
    await page.locator('#notice').filter({ hasText: '연습하고 싶은 내용을 입력' }).waitFor();
    assert.equal(payloads.length, 0);
    const message = '게임 재화 분석 문제 내줘. 최근 7일 획득과 소비를 비교하고 같은 주제를 반복 연습하고 싶어.';
    await page.locator('#training-request').fill(message);
    await page.locator('#request-level').selectOption('advanced');
    await page.locator('#request-sql-level').selectOption('beginner');
    await page.locator('#request-training').click();
    await page.locator('#clarification').waitFor({ state: 'visible' });
    assert.deepEqual({ ...payloads[0], request_id: null }, { contract_version: 'request-v2',
      request_id: null, message, difficulty: 'advanced', task_kind: 'auto', domain: 'auto',
      data_mode: 'adaptive', user_count: 200, intentional_repeat: false, sql_level: 'beginner' });
    await page.reload();
    await page.locator('#clarification').waitFor({ state: 'visible' });
    assert.equal(await page.locator('#training-request').inputValue(), message);
    await page.locator('#clarification-message').fill('최근 7일');
    await page.locator('#send-clarification').click();
    await page.locator('#retry-training').waitFor({ state: 'visible' });
    await page.locator('#retry-training').click();
    await page.locator('#clarification').waitFor({ state: 'visible' });
    await page.locator('#cancel-training').click();
    await page.locator('#request-status').filter({ hasText: '취소' }).waitFor();
    await load();
    await page.locator('#training-request').fill('재화 분석 문제 내줘');
    rejectPost = true;
    await page.locator('#request-training').click();
    await page.locator('#request-status').filter({ hasText: '요청 실패' }).waitFor();
    assert.equal(await page.locator('#training-request').inputValue(), '재화 분석 문제 내줘');
    assert.equal('sql_level' in payloads.at(-1), false);
    await load();
    await page.screenshot({ path: path.join(output, 'home-1280.png'), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await page.screenshot({ path: path.join(output, 'home-390.png'), fullPage: true });
    assert.equal(apiPaths.includes('/api/training/recommendation'), false);
    assert.equal(apiPaths.includes('/api/training/capabilities'), false);
    assert.equal(apiPaths.includes('/api/packages'), false);
    assert.deepEqual(errors, []);
    await fs.writeFile(path.join(output, 'result.json'), JSON.stringify({ actual_browser: true,
      live_api_reads: true, preparation_responses: 'simulated', passed: [
        'removed controls', 'empty request', 'difficulty and SQL payload', 'natural-language conditions',
        'clarification', 'refresh resume', 'failure and retry', 'cancel', 'input preservation',
        'SQL unspecified omitted', 'desktop and mobile', 'no page errors', 'obsolete APIs not called',
        'default new tab', 'resume tab visibility', 'tab input preservation', 'keyboard tabs',
        'tab refresh persistence', 'resume desktop and mobile', 'open existing training',
        'workspace tabs isolated', 'return to resume tab', 'empty training list' ], errors }, null, 2));
    console.log('Browser verification passed: 23 checks');
  } finally { await browser.close(); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
