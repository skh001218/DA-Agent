// Actual local UI checks; preserves pre-existing attempts and human approval states.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const fs=require('node:fs/promises');
const assert=require('node:assert/strict');
const base='http://127.0.0.1:8087';
const out='tests/artifacts/prd-v2-browser-2026-10-05';
const result={actual_browser:true,actual_api:true,provider_mocked:false,checks:{},flows:[],console_errors:[],retained_samples:[]};
async function main(){
 await fs.mkdir(out,{recursive:true});
 const browser=await chromium.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE});
 const context=await browser.newContext({acceptDownloads:true,viewport:{width:1280,height:900}});
 const page=await context.newPage();
 page.on('pageerror',e=>result.console_errors.push(e.message));
 page.on('dialog',d=>d.type()==='beforeunload'?d.accept():d.dismiss());
 const api=async p=>{const r=await context.request.get(base+p);assert.ok(r.ok(),p);return r.json();};
 const home=async()=>{await page.goto(base);await page.locator('#recommendation').filter({hasText:'제안'}).waitFor();};
 const responseFor=s=>page.waitForResponse(r=>r.url().endsWith(s)&&r.request().method()==='POST',{timeout:150000});
 const begin=async(message,level,kind,button='#request-training')=>{
  await home();await page.locator('#change-recommendation').click();
  await page.locator('#training-request').fill(message);await page.locator('#request-level').selectOption(level);
  await page.locator('#request-kind').selectOption(kind);await page.locator('#request-data').selectOption('existing');
  const response=responseFor('/api/training/requests');await page.locator(button).click();const started=await(await response).json();
  const deadline=Date.now()+140000;let req;
  do{req=await api('/api/training/requests/'+started.request_id);if(['ready','failed','cancelled','needs_clarification'].includes(req.status))break;await page.waitForTimeout(400);}while(Date.now()<deadline);
  return req;
 };
 const check=async(name,fn)=>{try{result.checks[name]={passed:true,...await fn()};}catch(e){result.checks[name]={passed:false,error:e.message};}await fs.writeFile(out+'/result.json',JSON.stringify(result,null,2));};
 try{
  await home();
  await check('learning_revision_conflict',async()=>{
   await page.getByText('학습 상태와 사용자 이견',{exact:true}).click();
   const before=await api('/api/learning-state');
   await context.request.patch(base+'/api/learning-state',{data:{expected_revision:before.state_revision,preferences:before.preferences,overrides:[]}});
   await page.locator('#save-learning').click();await page.locator('#notice').filter({hasText:'최신 상태'}).waitFor();
   assert.equal(await page.locator('#learning-goal').inputValue(),before.preferences.goal||'');
   await page.locator('#refresh-learning').click();return {input_preserved:true};
  });
  await check('delete_cancel_keyboard',async()=>{
   let deletes=0;const handler=r=>{if(r.method()==='DELETE')deletes++;};page.on('request',handler);
   const count=await page.locator('#attempts article').count();
   await page.locator('#attempts article').first().getByRole('button',{name:'훈련 삭제',exact:true}).focus();await page.keyboard.press('Enter');
   await page.getByRole('dialog').waitFor();assert.equal(await page.getByRole('button',{name:'삭제 취소',exact:true}).evaluate(n=>n===document.activeElement),true);
   await page.keyboard.press('Escape');assert.equal(await page.getByRole('dialog').count(),0);assert.equal(await page.locator('#attempts article').count(),count);assert.equal(deletes,0);page.off('request',handler);return {delete_requests:deletes};
  });
  await check('unsupported_input_preserved',async()=>{
   const msg='매출 결제 분석 문제를 만들어줘';const req=await begin(msg,'auto','auto');assert.equal(req.error_code,'unsupported_scope');assert.equal(req.planning_calls,0);assert.equal(await page.locator('#training-request').inputValue(),msg);return {request_id:req.request_id,status:req.status};
  });
  await check('clarification_answer',async()=>{
   let req=await begin('고급 D1~D7 미재접속 업무 요청 구체화 문제를 원합니다.','beginner','calculation');assert.equal(req.status,'needs_clarification');
   await page.locator('#clarification-message').fill('선택한 초급 지표 계산으로 진행해주세요. D1~D7 미재접속을 계산하겠습니다.');
   const response=responseFor('/clarify');await page.locator('#send-clarification').click();await response;
   const deadline=Date.now()+140000;do{req=await api('/api/training/requests/'+req.request_id);if(['ready','failed','needs_clarification'].includes(req.status))break;await page.waitForTimeout(500);}while(Date.now()<deadline);
   if(req.attempt_id)result.retained_samples.push({purpose:'clarification',attempt_id:req.attempt_id});
   assert.equal(req.status,'ready');return {request_id:req.request_id,attempt_id:req.attempt_id};
  });
  for(const [kind,level] of [['calculation','beginner'],['review','intermediate'],['design','advanced'],['investigation','advanced']]){
   const flow={kind,level};result.flows.push(flow);
   try{
    const label={calculation:'지표 계산',review:'분석 오류 수정',design:'업무 요청 구체화',investigation:'집단·기간 현상 조사'}[kind];
    const req=await begin(`접속 데이터의 D1~D7 미재접속 ${label} 문제를 내줘. 선택한 난이도와 유형으로 진행해줘.`,level,kind);
    flow.request_id=req.request_id;flow.request_status=req.status;flow.planning_calls=req.planning_calls;assert.equal(req.status,'ready',req.error||JSON.stringify(req.questions));
    flow.attempt_id=req.attempt_id;result.retained_samples.push({purpose:kind,attempt_id:req.attempt_id});
    await page.waitForURL('**/?attempt='+req.attempt_id);await page.locator('#workspace').waitFor();
    const initial=await api('/api/attempts/'+req.attempt_id);assert.equal(initial.task_kind,kind);assert.equal(initial.difficulty,level);
    assert.equal(await page.locator('#automatic-coaching').isChecked(),false);
    await page.locator('#sql').fill('SELECT missing_column FROM users');const bad=responseFor('/execute');await page.locator('#execute').click();const badResult=await(await bad).json();assert.equal(badResult.status,'error');assert.equal(await page.locator('#sql').inputValue(),'SELECT missing_column FROM users');flow.sql_error_preserved=true;
    await page.locator('#sql').fill('SELECT count(*) AS users FROM users');const execution=responseFor('/execute');await page.locator('#execute').focus();await page.keyboard.press('Enter');const executed=await(await execution).json();assert.equal(executed.status,'success');
    await page.locator('#executions .execution').filter({hasText:'SELECT count(*) AS users FROM users'}).getByRole('button',{name:'기록에 저장',exact:true}).click();await page.getByRole('button',{name:'저장 완료',exact:true}).waitFor();flow.sql_saved=true;
    await page.locator('#coach-message').fill('전체 유저 수만 확인했습니다. 미재접속을 판단하려면 다음에 어떤 조건을 확인해야 하나요?');
    await page.locator('#coach-evidence').selectOption({index:1});const coaching=responseFor('/conversation');await page.locator('#coach').click();const coached=await(await coaching).json();flow.coaching_status=coached.status;flow.coaching_error=coached.error;
    await page.locator('[data-section="problem_definition"]').fill('신규 유저의 가입 기간·고유 유저 분모·D1~D7 기간·D8 관측 완료 조건을 먼저 확인한다.');
    await page.locator('[data-section="hypothesis"]').fill('집단 구성과 관측 조건 차이를 확인한다. 세션 JOIN 행 수는 고유 유저 수와 다르며, 차이만으로 원인을 단정할 수 없다.');
    await page.locator('nav button[data-tab="report"]').click();
    await page.locator('#discoveries').fill(`전체 유저 수는 ${executed.rows[0][0]}명이다. 기간·관측 조건을 적용한 미재접속 수치는 아직 계산하지 않았으므로 미확인이다.`);
    await page.locator('[data-section="limitations"]').fill('전체 유저 수만으로 미재접속이나 원인을 판단할 수 없다. 고유 유저·기간·관측 조건을 검증해야 한다.');
    await page.locator('[data-section="next_actions"]').fill('분석 대상을 고정하고 고유 유저별 기간 내 재접속과 관측 완료를 확인한 뒤 집단 구성 차이를 비교한다.');
    await page.locator('#claims input[type=checkbox]').first().check();
    const submit=responseFor('/reports');await page.locator('#submit-report').click();const report=await(await submit).json();flow.report_id=report.report_id;
    const review=responseFor('/review');await page.getByRole('button',{name:'이 제출본 리뷰 요청',exact:true}).last().click();const reviewed=await(await review).json();flow.review_status=reviewed.status;flow.review_error=reviewed.error;
    // Exercise revision flow even if the model rejects a review; retain failures.
    await page.getByRole('button',{name:'이 제출본을 수정',exact:true}).last().click();
    await page.locator('[data-section="next_actions"]').fill('수정: 관측 완료 여부와 고유 유저 분모를 먼저 검산하고 동일 조건으로 집단별 수치와 표본 수를 비교한다. 원인 확정에는 추가 자료가 필요하다.');
    const revision=responseFor('/reports');await page.locator('#submit-report').click();const revised=await(await revision).json();flow.revised_report_id=revised.report_id;assert.equal(revised.report_version,2);
    await page.reload();await page.locator('#workspace').waitFor();const reopened=await api('/api/attempts/'+req.attempt_id);
    assert.equal(reopened.reports.length,2);assert.equal(reopened.saved_executions.length,1);assert.equal(reopened.package_id,initial.package_id);assert.ok(reopened.messages.length);assert.equal(reopened.reports.at(-1).previous_report_id,report.report_id);flow.resume=true;
    await page.locator('nav button[data-tab="analysis"]').click();assert.equal(await page.locator('#sql').inputValue(),'');
    await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    await page.screenshot({path:out+'/'+kind+'-390.png',fullPage:true});await page.setViewportSize({width:1280,height:900});flow.narrow_screen=true;flow.passed=reviewed.status==='completed'&&coached.status==='completed';
   }catch(e){flow.passed=false;flow.error=e.message;}
   await fs.writeFile(out+'/result.json',JSON.stringify(result,null,2));console.log(JSON.stringify({flow:kind,passed:flow.passed,error:flow.error,review_status:flow.review_status}));
  }
  await check('recommendation_start',async()=>{
   const req=await begin('접속 훈련','auto','auto','#start-recommendation');assert.equal(req.status,'ready');result.retained_samples.push({purpose:'recommendation',attempt_id:req.attempt_id});await page.waitForURL('**/?attempt='+req.attempt_id);await page.locator('#workspace').waitFor();return {request_id:req.request_id,attempt_id:req.attempt_id};
  });
  await check('metrics_download_and_quality_ui',async()=>{
   await page.goto(base+'/static/quality.html');await page.locator('#metrics-tab').click();await page.locator('input[name=domain]').fill('access');await page.locator('#refresh-metrics').click();await page.waitForFunction(()=>document.querySelectorAll('#metric-cards article').length===8);
   for(const format of ['json','csv']){const download=page.waitForEvent('download');await page.locator('#download-metrics-'+format).click();const file=await download;assert.equal(await file.failure(),null);await file.saveAs(out+'/metrics.'+format);const data=await fs.readFile(out+'/metrics.'+format,'utf8');if(format==='json')assert.equal(JSON.parse(data).filters.domain,'access');else assert.ok(data.startsWith('metric,field,value,metrics_version'));}
   await page.locator('#v2-quality-tab').click();await page.locator('#v2-quality-attempt option').first().waitFor({state:'attached'});await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:out+'/quality-390.png',fullPage:true});return {eight_metrics:true,json:true,csv:true};
  });
 }finally{result.finished_at=new Date().toISOString();await fs.writeFile(out+'/result.json',JSON.stringify(result,null,2));await browser.close();}
 console.log(JSON.stringify({checks:result.checks,flows:result.flows,retained_samples:result.retained_samples}));
}
main().catch(e=>{console.error(e.message);process.exitCode=1;});
