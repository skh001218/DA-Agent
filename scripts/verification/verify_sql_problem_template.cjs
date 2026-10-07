// Browser check of real transport capture; no Discord publication or model calls.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { pathToFileURL } = require('node:url');

async function main() {
  const out = path.resolve('tests/artifacts/sql-problem-template-2026-10-07');
  const browser = await chromium.launch({headless:true, channel:'msedge'});
  const page = await browser.newPage({viewport:{width:1280,height:1000}});
  const errors=[];
  page.on('pageerror', error=>errors.push(error.message));
  await page.goto(pathToFileURL(path.join(out,'index.html')).href);
  for (const kind of ['generated','tutorial']) {
    for (const level of ['beginner','intermediate','advanced']) {
      const article = page.locator('#'+kind+'-'+level);
      assert.equal(await article.locator('.image').count(), kind==='generated'?2:3);
      const text = await article.innerText();
      assert(text.includes('5. 제출 방법') && text.includes('1,900자'));
      const order = await article.locator(':scope > .message, :scope > .image').evaluateAll(nodes=>nodes.map(node=>node.className));
      assert.equal(order[0],'message');
      assert.equal(order[1],'image');
      assert.equal(order[kind==='generated'?3:4],'message');
    }
  }
  const first = page.locator('#generated-intermediate .image').first();
  await first.click();
  await page.locator('dialog[open] img').waitFor({state:'visible'});
  await page.waitForFunction(()=>document.querySelector('dialog img').naturalWidth===1200);
  await page.screenshot({path:path.join(out,'image-expanded.png')});
  await page.locator('#close').click();
  assert.equal(await page.locator('dialog[open]').count(),0);
  await page.locator('#generated-intermediate').screenshot({path:path.join(out,'desktop.png')});
  await page.setViewportSize({width:390,height:844});
  await page.locator('#generated-intermediate').screenshot({path:path.join(out,'mobile.png')});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth));
  await page.reload();
  assert.equal(await page.locator('#attachment-failure .image').count(),0);
  assert((await page.locator('#attachment-failure').innerText()).includes('컬럼명'));
  assert.equal(errors.length,0);
  await fs.writeFile(path.join(out,'browser.json'), JSON.stringify({local_preview:true,production_discord:false,
    cases:6,image_expansion:true,close:true,refresh:true,mobile_no_overflow:true,attachment_fallback:true,errors},null,2));
  await browser.close();
  console.log('6 SQL layouts, image expansion, mobile, refresh and fallback passed.');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
