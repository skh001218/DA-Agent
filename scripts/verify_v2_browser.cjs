// Optional Playwright verification against the actual local application.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs = require('node:fs/promises');
const path = require('node:path');
const assert = require('node:assert/strict');
async function main() {
  const output = path.resolve('tests/browser-v2'); await fs.mkdir(output,{recursive:true});
  const browser = await chromium.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE || undefined});
  const context = await browser.newContext({acceptDownloads:true,viewport:{width:1280,height:900}});
  const page = await context.newPage(); const errors=[];const deletes=[];
  page.on('pageerror',error=>errors.push(error.message));
  page.on('request',request=>{if(request.method()==='DELETE')deletes.push(request.url());});
  const base=process.env.VERIFY_BASE_URL || 'http://127.0.0.1:8087';
  const checks={};
  try {
    await page.goto(base); await page.locator('#attempts article').first().waitFor();
    const count=await page.locator('#attempts article').count();
    await page.locator('#attempts article').first().getByRole('button',{name:'훈련 삭제',exact:true}).click();
    await page.getByRole('button',{name:'삭제 취소',exact:true}).click();
    assert.equal(await page.locator('#attempts article').count(),count);assert.equal(deletes.length,0);
    checks.delete_cancel=true;
    await page.getByText('학습 상태와 사용자 이견',{exact:true}).click();
    const prior=await (await page.request.get(base+'/api/learning-state')).json();
    await page.locator('#save-learning').click();
    await page.waitForFunction(()=>document.querySelector('#notice').textContent.includes('저장'));
    const after=await (await page.request.get(base+'/api/learning-state')).json();
    assert.equal(after.state_revision,prior.state_revision+1);assert.deepEqual(after.preferences,prior.preferences);
    checks.learning_save=true;
    await page.screenshot({path:path.join(output,'home-1280.png'),fullPage:true});
    await page.goto(base+'/static/quality.html');await page.locator('#metrics-tab').click();
    await page.locator('#metric-cards article').first().waitFor();
    await page.locator('input[name=domain]').fill('access');await page.locator('#refresh-metrics').click();
    await page.waitForFunction(()=>document.querySelectorAll('#metric-cards article').length===8);
    checks.metrics_eight=true;
    for (const format of ['json','csv']) {
      const [download]=await Promise.all([page.waitForEvent('download'),page.locator('#download-metrics-'+format).click()]);
      const failure=await download.failure();assert.equal(failure,null);
      const target=path.join(output,'quality-metrics.'+format);await download.saveAs(target);
      const content=await fs.readFile(target,'utf8');assert.ok(content.length>100);
      if(format==='json')assert.equal(JSON.parse(content).filters.domain,'access');
      else assert.ok(content.startsWith('metric,field,value,metrics_version'));
      checks['download_'+format]=true;
    }
    await page.locator('#v2-quality-tab').click();await page.locator('#v2-quality-attempt option').first().waitFor({state:'attached'});
    assert.equal(await page.locator('#start-v2-quality').isEnabled(),true);checks.quality_v2_controls=true;
    await page.locator('#metrics-tab').click();await page.setViewportSize({width:390,height:844});
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));checks.width_390=true;
    await page.screenshot({path:path.join(output,'operator-390.png'),fullPage:true});
    assert.equal(errors.length,0);checks.console_clean=true;
  } finally {
    await fs.writeFile(path.join(output,'result.json'),JSON.stringify({actual_browser:true,actual_api:true,checks,console_errors:errors,delete_requests:deletes.length},null,2));
    await browser.close();
  }
  console.log(JSON.stringify(checks));
}
main().catch(error=>{console.error(error.message);process.exitCode=1;});
