// Continue only the dedicated task created by verify_adaptive_browser.cjs.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const fs=require('node:fs/promises');const assert=require('node:assert/strict');
async function main(){
 const output='tests/adaptive-browser-2026-10-05';
 const evidence=JSON.parse(await fs.readFile(output+'/result.json','utf8'));
 const item=evidence.cases.find(x=>x.name==='abnormal-users'&&x.status==='ready');assert.ok(item);
 const base=process.env.VERIFY_BASE_URL||'http://127.0.0.1:8087';
 const browser=await chromium.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE});
 const page=await browser.newPage({viewport:{width:1280,height:900}});
 const result={actual_browser:true,actual_api:true,actual_model:true,attempt_id:item.attempt_id};
 try{
  await page.goto(base+'/?attempt='+item.attempt_id);await page.locator('#workspace').waitFor();
  await page.locator('#coach-message').fill('활동량이 많은 이용자를 바로 제재해도 될까? 현재 공개 데이터에서 어떤 근거를 더 확인해야 하나?');
  const coaching=page.waitForResponse(r=>r.url().endsWith('/conversation')&&r.request().method()==='POST',{timeout:150000});
  await page.locator('#coach').click();const coached=await (await coaching).json();
  result.coaching_status=coached.status;result.coaching_feedback=coached.feedback;
  assert.equal(coached.status,'completed',JSON.stringify(coached));
  await page.locator('nav button[data-tab="report"]').click();
  const attempt=await (await page.request.get(base+'/api/attempts/'+item.attempt_id)).json();
  const count=attempt.saved_executions.at(-1).result.rows[0][0];
  await page.locator('#discoveries').fill(`확인한 전체 계정은 ${count}개다. 아직 간격 변동계수별 분포와 의심 계정 비율은 계산하지 않았다.`);
  await page.locator('[data-section="limitations"]').fill('계정 수 집계만으로 자동화 여부를 판정할 수 없다. 활동량과 간격의 변동성, 정상 반례를 추가로 비교해야 한다.');
  await page.locator('[data-section="next_actions"]').fill('활동량과 간격 변동계수를 비교하고 정상 고활동 이용자를 오인하지 않는지 검토한다. 제재 전에 추가 로그를 확인한다.');
  const check=page.locator('#claims input[type="checkbox"]').last();await check.check();
  const submission=page.waitForResponse(r=>r.url().endsWith('/reports')&&r.request().method()==='POST');
  await page.locator('#submit-report').click();const submitted=await (await submission).json();result.report_id=submitted.report_id;
  const review=page.waitForResponse(r=>r.url().endsWith('/review')&&r.request().method()==='POST',{timeout:150000});
  await page.getByRole('button',{name:'이 제출본 리뷰 요청',exact:true}).last().click();
  const reviewed=await (await review).json();result.review_status=reviewed.status;result.review_error=reviewed.error;result.review_feedback=reviewed.feedback;
  assert.equal(reviewed.status,'completed',JSON.stringify(reviewed));
  await page.screenshot({path:output+'/feedback.png',fullPage:true});
  await page.reload();await page.locator('#workspace').waitFor();
  const resumed=await(await page.request.get(base+'/api/attempts/'+item.attempt_id)).json();
  assert.ok(resumed.messages.length&&resumed.reports.some(x=>x.report_id===submitted.report_id)&&resumed.reviews.length);
  result.feedback_resume=true;result.status='pass';
 }catch(e){result.status='fail';result.failure=e.message;throw e;}
 finally{await fs.writeFile(output+'/feedback-result.json',JSON.stringify(result,null,2));await browser.close();}
 console.log(JSON.stringify(result));
}
main().catch(e=>{console.error(e.message);process.exitCode=1});
