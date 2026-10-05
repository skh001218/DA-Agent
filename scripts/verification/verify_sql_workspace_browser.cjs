// Real browser + PostgreSQL execution/save on an owned test attempt; no AI calls.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs = require('node:fs/promises');
const path = require('node:path');
const assert = require('node:assert/strict');
async function main() {
  const browser = await chromium.launch({ headless: true, channel: 'msedge' });
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await context.newPage();
  const base = process.env.VERIFY_BASE_URL || 'http://127.0.0.1:8087';
  const source = process.env.VERIFY_LOCAL_UI === '1';
  const output = path.resolve(process.env.VERIFY_OUTPUT_DIR || 'tests/artifacts/sql-workspace-browser-2026-10-05');
  await fs.mkdir(output, { recursive: true });
  const errors = [], consoleErrors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error' && !message.location().url.endsWith('/favicon.ico')) consoleErrors.push(message.text() + ' ' + message.location().url); });
  page.on('dialog', dialog => dialog.type() === 'beforeunload' ? dialog.accept() : dialog.dismiss());
  if (source) {
    const original = await context.request.get(base);
    const csp = original.headers()['content-security-policy'];
    await page.route(`${base}/**`, async route => {
      const pathname = new URL(route.request().url()).pathname;
      if (pathname.startsWith('/api/')) return route.continue();
      const file = pathname === '/' ? 'index.html' : path.basename(pathname);
      await route.fulfill({ body: await fs.readFile(path.resolve('src/da_agent/static', file)),
        contentType: file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html',
        headers: { 'Content-Security-Policy': csp } });
    });
  }
  let ownedId;
  const result = { actual_browser: true, actual_api: true, actual_database: true, actual_model: false,
    current_source_ui: source, checks: [], errors, consoleErrors };
  const check = name => result.checks.push(name);
  const enter = async text => {
    await page.locator('.CodeMirror').click();
    await page.keyboard.press('Control+a');
    await page.keyboard.insertText(text);
  };
  try {
    const packs = await (await context.request.get(`${base}/api/packages`)).json();
    const pack = packs.packages.find(pack => pack.problems.length && !/^(generated-|sample-)/.test(pack.package_id));
    assert.ok(pack, 'A verified package is required for the isolated test');
    const created = await context.request.post(`${base}/api/attempts`, { data: {
      package_id: pack.package_id, release_version: pack.release_version, problem_id: pack.problems[0].problem_id } });
    assert.ok(created.ok(), await created.text());
    ownedId = (await created.json()).attempt_id;
    await page.goto(`${base}/?attempt=${encodeURIComponent(ownedId)}`);
    await page.locator('#workspace').waitFor({ state: 'visible' });
    assert.equal(await page.locator('#analysis #schema').count(), 0);
    assert.equal(await page.locator('#analysis #execute').count(), 0);
    assert.equal(await page.locator('#analysis').isVisible(), true);
    check('analysis excludes dictionary and SQL workbench');
    await page.locator('[data-tab="sql-workspace"]').click();
    await page.locator('.CodeMirror').waitFor({ state: 'visible' });
    assert.equal(await page.locator('#schema').isVisible(), true);
    await page.locator('#execute').click();
    await page.locator('#notice').filter({ hasText: '실행할 SQL을 입력' }).waitFor();
    check('SQL tab and empty input guidance');
    const query = "-- SQL 편집기 확인\nSELECT 42 AS amount, '연습' AS label";
    await enter(query);
    const colors = await page.evaluate(() => Object.fromEntries(['keyword', 'string', 'number', 'comment']
      .map(token => [token, getComputedStyle(document.querySelector('.cm-' + token)).color])));
    assert.equal(new Set(Object.values(colors)).size, 4);
    assert.equal(await page.locator('.CodeMirror-linenumber').count() > 0, true);
    assert.equal(await page.evaluate(() => document.querySelector('#sql').value), query);
    check('typed PostgreSQL syntax colors and line numbers');
    await page.keyboard.press('Control+a');
    await page.keyboard.insertText('SELECT 7');
    await page.keyboard.press('Control+z');
    assert.equal(await page.evaluate(() => window.sqlEditor.getValue()), query);
    check('editing and undo preserve source');
    await page.locator('[data-tab="analysis"]').click();
    await page.locator('[data-tab="sql-workspace"]').click();
    assert.equal(await page.evaluate(() => window.sqlEditor.getValue()), query);
    check('tab changes preserve SQL');
    const executionResponse = page.waitForResponse(response => response.url().endsWith('/execute'));
    await page.locator('#execute').click();
    const execution = await (await executionResponse).json();
    assert.equal(execution.status, 'success', JSON.stringify(execution));
    assert.deepEqual(execution.rows, [[42, '연습']]);
    await page.getByRole('button', { name: '기록에 저장', exact: true }).first().click();
    await page.getByRole('button', { name: '저장 완료', exact: true }).waitFor();
    const stored = await (await context.request.get(`${base}/api/attempts/${ownedId}`)).json();
    assert.equal(stored.saved_executions.at(-1).sql, query);
    check('real SQL execution and exact source saved');
    await enter('SELECT missing_sql_editor_column FROM users');
    await page.locator('#execute').click();
    await page.locator('#executions .execution').first().locator('.error').waitFor();
    assert.equal(await page.evaluate(() => window.sqlEditor.getValue()), 'SELECT missing_sql_editor_column FROM users');
    check('SQL failure keeps input and results');
    await enter(query);
    await page.screenshot({ path: path.join(output, source ? 'source-sql-1280.png' : 'live-sql-1280.png'), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    const longQuery = "SELECT '" + '한글'.repeat(120) + "' AS label";
    await enter(longQuery);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    assert.equal(await page.locator('.CodeMirror-scroll').evaluate(node => node.scrollWidth <= node.clientWidth + 30), true);
    await page.screenshot({ path: path.join(output, source ? 'source-sql-390.png' : 'live-sql-390.png'), fullPage: true });
    check('390px layout and long line input');
    await page.reload();
    await page.locator('#workspace').waitFor({ state: 'visible' });
    await page.locator('[data-tab="sql-workspace"]').click();
    assert.equal(await page.evaluate(() => window.sqlEditor.getValue()), '');
    assert.equal(await page.locator('#executions .execution').count(), 0);
    await page.locator('[data-tab="history"]').click();
    assert.equal(await page.locator('#saved').textContent().then(text => text.includes(query)), true);
    check('refresh clears temporary SQL and restores saved evidence');
    // Confirm keyboard focus can leave the editor using Tab.
    await page.locator('[data-tab="sql-workspace"]').click();
    await page.locator('.CodeMirror').click();
    await page.keyboard.press('Tab');
    assert.equal(await page.evaluate(() => document.activeElement.id), 'execute');
    check('keyboard focus can leave editor');
    assert.deepEqual(errors, []);
    assert.deepEqual(consoleErrors, []);
    check('no browser or CSP errors');
    const fallback = await context.newPage();
    if (source) {
      await fallback.route(`${base}/**`, async route => {
        const pathname = new URL(route.request().url()).pathname;
        if (pathname.startsWith('/api/')) return route.continue();
        const file = pathname === '/' ? 'index.html' : path.basename(pathname);
        const emptyScript = ['codemirror.js', 'codemirror-sql.js'].includes(file);
        await route.fulfill({ body: emptyScript ? '' : await fs.readFile(path.resolve('src/da_agent/static', file)),
          contentType: file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html' });
      });
    } else await fallback.route(/\/static\/codemirror(?:-sql)?\.js$/, route => route.fulfill({ body: '', contentType: 'text/javascript' }));
    await fallback.goto(`${base}/?attempt=${encodeURIComponent(ownedId)}`);
    await fallback.locator('#workspace').waitFor({ state: 'visible' });
    await fallback.locator('[data-tab="sql-workspace"]').click();
    await fallback.locator('#sql').waitFor({ state: 'visible' });
    await fallback.locator('#sql').fill('SELECT 1');
    await fallback.locator('#execute').click();
    await fallback.locator('#executions').filter({ hasText: '상태: success' }).waitFor();
    await fallback.close({ runBeforeUnload: false });
    check('basic textarea fallback still executes SQL');
    result.status = 'passed';
  } catch (error) { result.status = 'failed'; result.failure = error.message; throw error; }
  finally {
    if (ownedId) {
      const cleanup = await context.request.delete(`${base}/api/attempts/${ownedId}`, { data: {} });
      result.owned_test_cleanup = cleanup.ok();
      if (!cleanup.ok()) result.cleanup_error = `Test cleanup failed: ${ownedId}`;
    }
    await fs.writeFile(path.join(output, source ? 'source-result.json' : 'live-result.json'), JSON.stringify(result, null, 2));
    await browser.close();
    if (result.cleanup_error) throw new Error(result.cleanup_error);
  }
  console.log(`SQL workspace verification passed: ${result.checks.length} checks; owned test cleaned up.`);
}
main().catch(error => { console.error(error); process.exitCode = 1; });
