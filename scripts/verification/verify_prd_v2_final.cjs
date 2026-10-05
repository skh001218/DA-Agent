// Targeted real UI review, retry, cancellation, and pending-assessment conflict checks.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');const fs=require('node:fs/promises');const assert=require('node:assert/strict');
const base='http://127.0.0.1:8087',out='tests/artifacts/prd-v2-browser-2026-10-05';
async function main(){
 const previous=JSON.parse(await fs.readFile(out+'/result.json','utf8'));const resumed=JSON.parse(await fs.readFile(out+'/resume.json','utf8'));
 const result={actual_browser:true,actual_api:true,checks:{},reviews:[],console_errors:[]};
 const browser=await chromium.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE});const context=await browser.newContext({viewport:{width:1280,height:900}});const page=await context.newPage();page.on('pageerror',e=>result.console_errors.push(e.message));page.on('dialog',d=>d.type()==='beforeunload'?d.accept():d.dismiss());
 const api=async p=>(await context.request.get(base+p)).json();const response=s=>page.waitForResponse(r=>r.url().endsWith(s)&&r.request().method()==='POST',{timeout:150000});
 const check=async(name,fn)=>{try{result.checks[name]={passed:true,...await fn()};}catch(e){result.checks[name]={passed:false,error:e.message};}await fs.writeFile(out+'/final.json',JSON.stringify(result,null,2));};
 try{
  await check('retry_preserved_fixed_request',async()=>{
   const initial=JSON.parse(await fs.readFile(out+'/initial-missing-schema.json','utf8'));const rid=initial.flows[0].request_id;const before=await api('/api/training/requests/'+rid);
   if(before.status==='ready'){const proof=JSON.parse(await fs.readFile(out+'/initial-report-selection.json','utf8')).checks.retry_preserved_fixed_request;assert.ok(proof.passed);return {...proof,evidence:'initial-report-selection.json; original successful retry preserved'};}assert.equal(before.status,'failed');
   await page.goto(base+'/?request='+rid);await page.locator('#retry-training').waitFor();const retry=response('/retry');await page.locator('#retry-training').click();await retry;await page.waitForURL('**/?attempt=*',{timeout:150000});await page.locator('#workspace').waitFor();const after=await api('/api/training/requests/'+rid);assert.equal(after.status,'ready');assert.equal(after.planning_calls,before.planning_calls);return {request_id:rid,attempt_id:after.attempt_id,new_planning_calls:0};
  });
  await check('request_cancel',async()=>{
   await page.goto(base);await page.locator('#recommendation').filter({hasText:'제안'}).waitFor();await page.locator('#training-request').fill('접속 데이터로 초급 D1~D7 미재접속 계산 문제를 내줘.');await page.locator('#request-level').selectOption('beginner');await page.locator('#request-kind').selectOption('calculation');await page.locator('#request-data').selectOption('existing');const started=response('/api/training/requests');await page.locator('#request-training').click();const req=await(await started).json();await page.locator('#cancel-training').waitFor();const cancelled=response('/cancel');await page.locator('#cancel-training').click();const value=await(await cancelled).json();assert.equal(value.status,'cancelled');await page.waitForTimeout(5000);assert.equal((await api('/api/training/requests/'+req.request_id)).status,'cancelled');return {request_id:req.request_id,failed_task_unpublished:true};
  });
  await check('assessment_revision_conflict',async()=>{
   const scratch=await(await context.request.post(base+'/api/attempts',{data:{package_id:'training-001',release_version:'v1',problem_id:'problem-001'}})).json();assert.ok(scratch.attempt_id);
   await page.goto(base+'/static/quality.html');await page.locator('#metrics-tab').click();await page.locator('#assessment-kind').selectOption('difficulty');await page.locator('#assessment-target').fill(scratch.attempt_id);await page.locator('#assessment-version').fill('v1');await page.locator('#assessment-verdict').selectOption('pending');await page.locator('#assessment-reviewer').fill('자동 UI 검증 · 의미 판정 아님');await page.locator('#assessment-note').fill('수정 충돌 검사 전용 미판정 표본. 실제 사람 품질 승인이 아니다.');
   const created=response('/api/quality/assessments');await page.locator('#save-assessment').click();const assessment=await(await created).json();assert.equal(assessment.result,'pending');
   const row=page.locator('#assessments article').filter({hasText:scratch.attempt_id});await row.waitFor();await row.locator('textarea').fill('충돌 후에도 유지할 검증 입력');
   await context.request.patch(base+'/api/quality/assessments/'+assessment.assessment_id,{data:{expected_revision:1,result:'pending',reviewer:'자동 UI 검증 · 의미 판정 아님',note:'다른 화면 수정 모의 행동. 판정은 미판정 유지.'}});
   await row.getByRole('button',{name:'판정 수정 저장',exact:true}).click();await page.locator('#notice').filter({hasText:'최신 판정'}).waitFor();assert.equal(await row.locator('textarea').inputValue(),'충돌 후에도 유지할 검증 입력');
   const deleted=await context.request.delete(base+'/api/attempts/'+scratch.attempt_id,{data:{}});assert.ok(deleted.ok());assert.ok(!(await api('/api/quality/assessments')).assessments.some(a=>a.assessment_id===assessment.assessment_id));return {input_preserved:true,pending_fixture_deleted:true};
  });
  for(const item of resumed.flows){const review={kind:item.kind,attempt_id:item.attempt_id};result.reviews.push(review);try{
   await page.goto(base+'/?attempt='+item.attempt_id);await page.locator('#workspace').waitFor();await page.locator('nav button[data-tab="history"]').click();
   const responsePromise=response('/review');await page.locator('#reports .report-entry').first().getByRole('button',{name:'이 제출본 리뷰 요청',exact:true}).click();const value=await(await responsePromise).json();review.status=value.status;review.error=value.error;review.total_score=value.feedback?.total_score;
   await page.reload();await page.locator('#workspace').waitFor();const after=await api('/api/attempts/'+item.attempt_id);assert.ok(after.reviews.some(r=>r.report_id===item.revised_report_id));assert.equal(after.reports.length,2);review.record_preserved=true;
   await page.locator('nav button[data-tab="analysis"]').click();await page.locator('summary').filter({hasText:'기준 SQL과 해설 보기'}).click();const explanation=response('/explanation');await page.locator('#explanation-button').click();await explanation;await page.reload();await page.locator('#workspace').waitFor();assert.ok((await api('/api/attempts/'+item.attempt_id)).explanation_viewed);review.explicit_explanation=true;
   review.passed=value.status==='completed';
  }catch(e){review.passed=false;review.failure=e.message;}await fs.writeFile(out+'/final.json',JSON.stringify(result,null,2));console.log(JSON.stringify(review));}
 }finally{await fs.writeFile(out+'/final.json',JSON.stringify(result,null,2));await browser.close();}
 console.log(JSON.stringify(result.checks));
}
main().catch(e=>{console.error(e.message);process.exitCode=1;});
