const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const fs=require('node:fs/promises');
const assert=require('node:assert/strict');
const base='http://127.0.0.1:8087';
const attempt='a1a57551-2c55-4dbd-b7d7-f9a14f1e1010'; // Retained verification fixture, not a learner attempt.
const result={actual_browser:true,actual_model:true,actual_db:true,attempt_id:attempt,checks:[],page_errors:[]};
async function main(){
 const browser=await chromium.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE});
 try{
  const page=await browser.newPage();page.on('pageerror',e=>result.page_errors.push(e.message));
  await page.goto(base+'/?attempt='+attempt);await page.locator('#workspace').waitFor();
  const before=await (await page.request.get(base+'/api/attempts/'+attempt)).json();
  // Korean question without raw data remains a persistent ordinary conversation.
  await page.locator('#coach-message').fill('저장한 결과의 근거를 보고 다음에 확인할 조건 하나를 알려주세요.');
  await page.locator('#coach-evidence').selectOption({index:1});
  const waiting=page.waitForResponse(r=>r.url().endsWith('/conversation')&&r.request().method()==='POST',{timeout:150000});
  await page.locator('#coach').click();const response=await (await waiting).json();
  assert.equal(response.status,'completed',JSON.stringify(response.error));
  assert.equal(response.prompt_version,'cumulative-coach-v3');
  assert.deepEqual(Object.keys(response.feedback).sort(),['action_type','reason','next_action','evidence_ids','uncertainty','evidence_state'].sort());
  await page.locator('#coach-result').filter({hasText:response.feedback.reason}).waitFor();
  result.checks.push({name:'actual_coaching',passed:true,response});
  await page.reload();await page.locator('#workspace').waitFor();
  await page.locator('#conversation-history').filter({hasText:response.feedback.reason}).waitFor();
  assert.ok(!(await page.locator('#conversation-history').innerText()).includes('[object Object]'));
  const after=await (await page.request.get(base+'/api/attempts/'+attempt)).json();
  assert.equal(after.messages.length,before.messages.length+1);
  result.checks.push({name:'reload_preserves_readable_feedback',passed:true});
  await page.locator('#temporary-question').check();
  await page.locator('#coach-message').fill('현재 근거에서 아직 확정할 수 없는 점 하나를 알려주세요.');
  const transient=page.waitForResponse(r=>r.url().endsWith('/conversation')&&r.request().method()==='POST',{timeout:150000});
  await page.locator('#coach').click();const temporary=await(await transient).json();
  assert.equal(temporary.status,'completed',JSON.stringify(temporary.error));assert.equal(temporary.transient,true);
  await page.locator('#coach-result').filter({hasText:'임시 질문'}).waitFor();
  await page.screenshot({path:'tests/prd-v2-browser-2026-10-05/coaching-template.png',fullPage:true});
  await page.reload();await page.locator('#workspace').waitFor();
  assert.equal(await page.locator('#coach-result').innerText(),'');
  const final=await(await page.request.get(base+'/api/attempts/'+attempt)).json();assert.equal(final.messages.length,after.messages.length);
  result.checks.push({name:'temporary_feedback_disappears_after_reload',passed:true,response:temporary});
  assert.equal(result.page_errors.length,0);result.passed=true;
 }catch(error){result.passed=false;result.error=error.message;process.exitCode=1;}
 finally{await fs.writeFile('tests/prd-v2-browser-2026-10-05/coaching-template.json',JSON.stringify(result,null,2));await browser.close();}
}
main().catch(error=>{console.error(error.message);process.exitCode=1;});
