
// Current UI matrix, owned verification attempts; no approvals or deletion.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const fs=require('node:fs/promises');const assert=require('node:assert/strict');
const base=process.env.VERIFICATION_BASE||'http://127.0.0.1:8087',out=process.env.VERIFICATION_OUT||'tests/prd-v2-revalidation-2026-10-05';
const result={actual_browser:true,actual_api:true,actual_model:true,flows:[],page_errors:[],started_at:new Date().toISOString()};
async function main(){
 await fs.mkdir(out,{recursive:true});
 const browser=await chromium.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE});
 const context=await browser.newContext({acceptDownloads:true,viewport:{width:1280,height:900}});
 const page=await context.newPage();page.on('pageerror',e=>result.page_errors.push(e.message));
 page.on('dialog',d=>d.type()==='beforeunload'?d.accept():d.dismiss());
 const api=async path=>{const r=await context.request.get(base+path);assert.ok(r.ok());return r.json();};
 const response=path=>page.waitForResponse(r=>r.url().endsWith(path)&&r.request().method()==='POST',{timeout:150000});
 const snapshot=()=>fs.writeFile(out+'/matrix.json',JSON.stringify(result,null,2));
 const labels={calculation:'지표 계산',review:'집계 오류 검토',design:'업무 요청 분석 설계',investigation:'플랫폼별 현상 조사'};
 try{
  for(const kind of Object.keys(labels))for(const level of ['beginner','intermediate','advanced']){
   if(process.env.VERIFICATION_FLOWS&&!process.env.VERIFICATION_FLOWS.split(',').includes(kind+':'+level))continue;
   const flow={kind,level};result.flows.push(flow);await snapshot();
   try{
    await page.goto(base);await page.locator('#request-training').waitFor();
    await page.waitForFunction(()=>typeof document.querySelector('#request-training')?.onclick==='function');
    await page.locator('#training-request').fill(`신규 유저 D1~D7 미재접속 ${labels[kind]} 연습입니다. 선택한 유형과 난이도로 진행해주세요.`);
    await page.locator('#request-kind').selectOption(kind);await page.locator('#request-level').selectOption(level);await page.locator('#request-data').selectOption('existing');
    const start=response('/api/training/requests');await page.locator('#request-training').click();const accepted=await(await start).json();flow.request_id=accepted.request_id;
    const poll=async()=>{let req;const end=Date.now()+150000;do{req=await api('/api/training/requests/'+accepted.request_id);if(['ready','failed','needs_clarification','cancelled'].includes(req.status))break;await page.waitForTimeout(400);}while(Date.now()<end);return req;};
    let req=await poll();flow.initial_status=req.status;
    if(req.status==='needs_clarification'){
     await page.locator('#clarification-message').fill('선택한 난이도와 유형을 유지합니다. 신규 유저 D1~D7 미재접속과 관측 완료 조건을 대상으로 하며 현상 조사는 플랫폼별 비교를 사용합니다.');
     const clarify=response('/clarify');await page.locator('#send-clarification').click();await clarify;req=await poll();flow.clarification=true;
    }
    flow.request_status=req.status;flow.planning_calls=req.planning_calls;
    if(req.status!=='ready'){flow.failure_detail=req.failure_detail;flow.diagnostics=await api('/api/quality/diagnostics?request_id='+req.request_id);}
    assert.equal(req.status,'ready',req.error);flow.attempt_id=req.attempt_id;
    await page.waitForURL('**/?attempt='+req.attempt_id);await page.locator('#workspace').waitFor();
    const initial=await api('/api/attempts/'+req.attempt_id);assert.equal(initial.task_kind,kind);assert.equal(initial.difficulty,level);flow.ready=true;flow.evaluation_version=initial.problem.evaluation_version;
    if(initial.problem.evaluation_version==='request-review-v3')assert.ok(initial.problem.evaluation_rubric);
    assert.equal(await page.locator('#automatic-coaching').isChecked(),false);
    await page.locator('#business-facts').click();await page.locator('#business-result').filter({hasText:'고정'}).waitFor();
    await page.locator('[data-section="problem_definition"]').fill('공개 가입 기간의 신규 유저를 고유 유저 단위로 정의하고 D1~D7 미재접속과 D8 관측 완료를 구분한다. 플랫폼 비교는 동일 대상과 관측 조건을 사용한다.');
    await page.locator('[data-section="hypothesis"]').fill('반복 세션의 JOIN 행 수는 고유 유저 수와 다르다. 유저별 재접속을 집계하고 관측 미완료를 제외한다. 플랫폼 구성과 기간 차이를 검증하며 관측 차이만으로 원인을 단정하지 않는다.');
    if(kind!=='design'){
     await page.locator('#sql').fill('SELECT missing_column FROM users');const bad=response('/execute');await page.locator('#execute').click();assert.equal((await(await bad).json()).status,'error');assert.equal(await page.locator('#sql').inputValue(),'SELECT missing_column FROM users');flow.sql_error_preserved=true;
     await page.locator('#sql').fill('SELECT count(*) AS users FROM users');const run=response('/execute');await page.locator('#execute').click();const execution=await(await run).json();assert.equal(execution.status,'success');
     await page.locator('#executions .execution').filter({hasText:'SELECT count(*) AS users FROM users'}).getByRole('button',{name:'기록에 저장',exact:true}).click();await page.getByRole('button',{name:'저장 완료',exact:true}).waitFor();flow.sql_saved=true;
    }else{flow.no_sql=true;assert.equal(initial.saved_executions.length,0);}
    await page.locator('#coach-message').fill('현재 정의와 분석 방법에서 다음에 확인할 조건 하나를 알려주세요.');
    if(kind!=='design')await page.locator('#coach-evidence').selectOption({index:1});
    const coach=response('/conversation');await page.locator('#coach').click();const coached=await(await coach).json();flow.coaching={status:coached.status,error:coached.error,action:coached.feedback?.action_type,prompt_version:coached.prompt_version};
    await page.locator('nav button[data-tab="report"]').click();
    await page.locator('#discoveries').fill(kind==='design'?'고유 유저·가입 기간·D1~D7·D8 관측 완료 조건으로 대상과 지표를 정의한다. 반복 세션을 제거하고 플랫폼별 분모와 비율을 같은 조건으로 비교하는 계획이다. 실제 계산 수치를 주장하지 않는다.':'전체 유저 수만 조회했다. 공개 기간과 미재접속 조건을 적용한 수치 및 플랫폼 차이는 아직 계산하지 않아 미확인이다.');
    await page.locator('[data-section="limitations"]').fill('관측 자료만으로 실제 원인을 확정할 수 없다. 기간·수집 완료·표본 구성의 영향을 확인한다. 아직 실행하지 않은 계산은 결과로 주장하지 않는다.');
    await page.locator('[data-section="next_actions"]').fill('동일 코호트와 D8 관측 완료를 확인하고 유저별 재접속을 집계한다. 플랫폼별 표본 수와 관측 조건을 맞춰 대안 설명을 비교한다.');
    if(kind!=='design')await page.locator('#claims input[type=checkbox]').first().check();
    const submit=response('/reports');await page.locator('#submit-report').click();const report=await(await submit).json();assert.equal(report.report_version,1);flow.report_id=report.report_id;
    const review=response('/review');await page.getByRole('button',{name:'이 제출본 리뷰 요청',exact:true}).first().click();const reviewed=await(await review).json();flow.review={status:reviewed.status,error:reviewed.error,criteria:reviewed.feedback?.criteria?.map(c=>({key:c.key,level:c.level,status:c.status})),score:reviewed.feedback?.total_score,score_status:reviewed.feedback?.score_status};
    if(reviewed.feedback?.score_status==='held')await page.getByText('총점 보류',{exact:false}).first().waitFor();
    if(kind==='design'&&reviewed.status==='completed')assert.ok(!reviewed.feedback.criteria.some(c=>c.key==='sql_accuracy'));
    await page.getByRole('button',{name:'이 제출본을 수정',exact:true}).first().click();
    await page.locator('[data-section="next_actions"]').fill('수정: 고유 유저 분모·기간·관측 완료를 먼저 검산하고, 같은 기준으로 플랫폼 구성과 표본 수를 비교한다. 실제 원인은 추가 자료로 확인한다.');
    const resubmit=response('/reports');await page.locator('#submit-report').click();const revision=await(await resubmit).json();assert.equal(revision.report_version,2);assert.equal(revision.previous_report_id,report.report_id);flow.revised_report_id=revision.report_id;
    await page.reload();await page.locator('#workspace').waitFor();const restored=await api('/api/attempts/'+req.attempt_id);
    assert.equal(restored.reports.length,2);assert.equal(restored.saved_executions.length,kind==='design'?0:1);assert.equal(restored.package_id,initial.package_id);assert.equal(await page.locator('#sql').inputValue(),'');
    if(coached.status==='completed'){assert.ok(restored.messages.length);await page.locator('#conversation-history').filter({hasText:coached.feedback.reason}).waitFor();}
    flow.resume=true;await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:out+'/'+kind+'-'+level+'-390.png',fullPage:true});await page.setViewportSize({width:1280,height:900});flow.narrow=true;
    flow.passed=coached.status==='completed'&&reviewed.status==='completed';
   }catch(error){flow.passed=false;flow.error=error.message;}
   await snapshot();console.log(JSON.stringify({kind,level,passed:flow.passed,request:flow.request_status,coaching:flow.coaching?.status,review:flow.review?.status,error:flow.error}));
  }
 }finally{result.finished_at=new Date().toISOString();await snapshot();await browser.close();}
}
main().catch(e=>{console.error(e.message);process.exitCode=1;});
