// Complete non-AI UI checks using this verification's own attempts.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const fs=require('node:fs/promises');const assert=require('node:assert/strict');
const base='http://127.0.0.1:8087',out='tests/prd-v2-browser-2026-10-05';
async function main(){
 const prior=JSON.parse(await fs.readFile(out+'/result.json','utf8'));
 const result={actual_browser:true,actual_api:true,model_calls:0,flows:[],checks:{},console_errors:[]};
 const browser=await chromium.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE});const context=await browser.newContext({viewport:{width:1280,height:900}});const page=await context.newPage();page.on('pageerror',e=>result.console_errors.push(e.message));page.on('dialog',d=>d.type()==='beforeunload'?d.accept():d.dismiss());
 const api=async p=>(await context.request.get(base+p)).json();const response=s=>page.waitForResponse(r=>r.url().endsWith(s)&&r.request().method()==='POST');
 try{
  for(const item of prior.flows){const flow={kind:item.kind,attempt_id:item.attempt_id};result.flows.push(flow);try{
   assert.ok(prior.retained_samples.some(x=>x.attempt_id===item.attempt_id));
   await page.goto(base+'/?attempt='+item.attempt_id);await page.locator('#workspace').waitFor();
   await page.locator('[data-section="problem_definition"]').fill('가입 기간·고유 유저 분모·D1~D7 미재접속·D8 관측 완료 조건을 정의한다.');await page.locator('[data-section="hypothesis"]').fill('집단 구성과 관측 기간 차이를 비교하고 JOIN 행 수를 고유 유저 수로 해석하지 않는다.');
   await page.locator('nav button[data-tab="report"]').click();await page.locator('#discoveries').fill('전체 유저 수만 확인했다. 미재접속 조건을 적용한 수치는 미확인이다.');await page.locator('[data-section="limitations"]').fill('유저 수만으로 미재접속률이나 원인을 확정할 수 없다.');await page.locator('[data-section="next_actions"]').fill('동일 코호트와 관측 조건으로 집단별 수치와 표본 수를 검산한다.');await page.locator('#claims input[type=checkbox]').first().check();
   const submit=response('/reports');await page.locator('#submit-report').click();const report=await(await submit).json();assert.equal(report.report_version,1);flow.report_id=report.report_id;
   await page.getByRole('button',{name:'이 제출본을 수정',exact:true}).last().click();await page.locator('[data-section="next_actions"]').fill('수정: D8 관측 완료와 고유 유저 분모를 먼저 검산하고 기간·집단 구성을 맞춰 비교한다. 추가 자료 없이는 원인을 확정하지 않는다.');
   const resubmit=response('/reports');await page.locator('#submit-report').click();const revised=await(await resubmit).json();assert.equal(revised.report_version,2);assert.equal(revised.previous_report_id,report.report_id);flow.revised_report_id=revised.report_id;
   await page.reload();await page.locator('#workspace').waitFor();const restored=await api('/api/attempts/'+item.attempt_id);assert.equal(restored.reports.length,2);assert.equal(restored.saved_executions.length,1);assert.equal(await page.locator('#sql').inputValue(),'');flow.resume=true;
   await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:out+'/'+item.kind+'-390.png',fullPage:true});await page.setViewportSize({width:1280,height:900});flow.narrow_screen=true;flow.passed=true;
  }catch(e){flow.passed=false;flow.error=e.message;}await fs.writeFile(out+'/resume.json',JSON.stringify(result,null,2));console.log(JSON.stringify(flow));}
  const scratch=await(await context.request.post(base+'/api/attempts',{data:{package_id:'training-001',release_version:'v1',problem_id:'problem-001'}})).json();assert.ok(scratch.attempt_id);result.scratch_id=scratch.attempt_id;
  await page.goto(base);await page.locator('#attempts article').filter({has:page.locator('button')}).first().waitFor();
  // Locate the owned attempt by its resume action; never delete an existing user attempt.
  const card=page.locator('#attempts article').filter({has:page.locator('button')});
  const owned=await card.evaluateAll((cards,id)=>cards.findIndex(c=>c.textContent.includes(id.slice(0,8))),scratch.attempt_id);
  let target;
  if(owned>=0)target=card.nth(owned);else{
   const ids=(await api('/api/attempts')).attempts;const index=ids.findIndex(a=>a.attempt_id===scratch.attempt_id);assert.ok(index>=0);target=page.locator('#attempts article').nth(index);
  }
  await target.getByRole('button',{name:'훈련 삭제',exact:true}).click();await page.getByRole('button',{name:'훈련 삭제 확정',exact:true}).click();await page.waitForFunction(()=>!document.querySelector('dialog'));
  assert.ok(!(await api('/api/attempts')).attempts.some(a=>a.attempt_id===scratch.attempt_id));result.checks.owned_delete_confirm={passed:true,attempt_id:scratch.attempt_id};
 }finally{await fs.writeFile(out+'/resume.json',JSON.stringify(result,null,2));await browser.close();}
}
main().catch(e=>{console.error(e.message);process.exitCode=1;});
