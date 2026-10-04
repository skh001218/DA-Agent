// Final targeted regression: unsupported guidance and narrow analysis layout.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs=require('node:fs/promises');
const assert=require('node:assert/strict');
async function main(){
 const browser=await chromium.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE});
 const page=await browser.newPage({viewport:{width:390,height:844}});
 const result={actual_browser:true,actual_api:true,model_calls:0};
 try{
  const base=process.env.VERIFY_BASE_URL || 'http://127.0.0.1:8087';
  await page.goto(base);
  await page.locator('#recommendation').filter({hasText:'제안'}).waitFor();
  const message='매출과 결제 데이터 분석을 연습하고 싶습니다';
  await page.locator('#training-request').fill(message);
  const response=page.waitForResponse(r=>r.url().endsWith('/api/training/requests') && r.request().method()==='POST');
  await page.locator('#request-training').click();
  const request=await (await response).json();
  await page.waitForFunction(()=>document.querySelector('#request-status').textContent.includes('접속 데이터'));
  const saved=await (await page.request.get(base+'/api/training/requests/'+request.request_id)).json();
  assert.equal(saved.error_code,'unsupported_scope');assert.equal(saved.planning_calls,0);
  assert.equal(await page.locator('#training-request').inputValue(),message);
  result.unsupported_guidance=true;result.input_preserved=true;
  await page.goto(base+'/?attempt=d58d26bc-4398-40c4-9f64-3c918d0c5a27');
  await page.locator('#workspace').waitFor();
  result.analysis_width=await page.evaluate(()=>({width:innerWidth,scrollWidth:document.documentElement.scrollWidth}));
  assert.ok(result.analysis_width.scrollWidth<=result.analysis_width.width);
  await page.screenshot({path:'tests/browser-v2/analysis-390.png',fullPage:true});
 }finally{
  await fs.writeFile('tests/verification-v2-final-browser-2026-10-04.json',JSON.stringify(result,null,2));
  await browser.close();
 }
 console.log(JSON.stringify(result));
}
main().catch(e=>{console.error(e.message);process.exitCode=1;});
