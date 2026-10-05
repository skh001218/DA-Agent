// Verify the deployed app without creating, changing, or deleting training records.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs = require('node:fs/promises');
const path = require('node:path');
const assert = require('node:assert/strict');
async function main() {
  const browser = await chromium.launch({ headless: true, channel: 'msedge' });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const base = process.env.VERIFY_BASE_URL || 'http://127.0.0.1:8087';
  const output = path.resolve('tests/artifacts/simple-training-browser-2026-10-05');
  const errors = [], mutations = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/api/**', async route => {
    if (route.request().method() !== 'GET') {
      mutations.push(route.request().url());
      return route.abort();
    }
    await route.continue();
  });
  try {
    await fs.mkdir(output, { recursive: true });
    await page.goto(base);
    await page.waitForFunction(() => typeof document.querySelector('#request-training')?.onclick === 'function');
    assert.equal(await page.locator('#new-training-panel').isVisible(), true);
    assert.equal(await page.locator('#attempts').isVisible(), false);
    for (const id of ['request-kind', 'request-domain', 'request-data', 'request-goal', 'request-time',
      'intentional-repeat', 'capability-status', 'recommendation', 'packages']) {
      assert.equal(await page.locator(`#${id}`).count(), 0);
    }
    await page.screenshot({ path: path.join(output, 'live-home-1280.png'), fullPage: true });
    await page.locator('#training-request').fill('탭 전환 확인용 요청');
    await page.getByRole('tab', { name: '이어 하기', exact: true }).click();
    await page.locator('#attempts article').first().waitFor();
    assert.equal(await page.locator('#new-training-panel').isVisible(), false);
    await page.screenshot({ path: path.join(output, 'live-resume-1280.png'), fullPage: true });
    await page.getByRole('tab', { name: '새 훈련', exact: true }).click();
    assert.equal(await page.locator('#training-request').inputValue(), '탭 전환 확인용 요청');
    await page.getByRole('tab', { name: '이어 하기', exact: true }).click();
    await page.reload();
    await page.waitForFunction(() => typeof document.querySelector('#request-training')?.onclick === 'function');
    assert.equal(await page.locator('#resume-training-panel').isVisible(), true);
    await page.locator('#attempts').getByRole('button', { name: '이어서 분석', exact: true }).first().click();
    await page.locator('#workspace').waitFor({ state: 'visible' });
    await page.getByRole('button', { name: '3. 보고서 작성', exact: true }).click();
    assert.equal(await page.locator('#report').isVisible(), true);
    await page.getByRole('button', { name: '훈련 목록', exact: true }).click();
    await page.locator('#resume-training-panel').waitFor({ state: 'visible' });
    assert.equal(await page.locator('#attempts').isVisible(), true);
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await page.screenshot({ path: path.join(output, 'live-resume-390.png'), fullPage: true });
    await page.getByRole('tab', { name: '새 훈련', exact: true }).click();
    await page.screenshot({ path: path.join(output, 'live-home-390.png'), fullPage: true });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    assert.deepEqual(errors, []);
    assert.deepEqual(mutations, []);
    await fs.writeFile(path.join(output, 'live-result.json'), JSON.stringify({ actual_browser: true,
      actual_deployed_app: true, base, status: 'passed', page_errors: errors,
      mutation_requests: mutations, checked: ['default new tab', 'removed controls', 'tab switch',
        'input preservation', 'refresh', 'existing training resume', 'workspace tabs',
        'return to list', '1280px and 390px layouts'] }, null, 2));
    console.log('Deployed app verified: desktop/mobile, tabs, refresh, existing training resume; no mutations or page errors.');
  } finally { await browser.close(); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
