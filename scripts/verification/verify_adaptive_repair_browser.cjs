// Actual browser + actual API/model: request-specific data, SQL, save and resume.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs = require('node:fs/promises');
const assert = require('node:assert/strict');
async function main() {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const base = process.env.VERIFY_BASE_URL || 'http://127.0.0.1:8087';
  const output = 'tests/artifacts/adaptive-repair-browser-2026-10-05';
  await fs.mkdir(output, { recursive: true });
  const result = { actual_browser: true, actual_api: true, actual_model: true, cases: [], errors: [] };
  page.on('pageerror', e => result.errors.push(e.message));
  const resumes = JSON.parse(process.env.RESUME_REQUESTS || '{}');
  const cases = [
    { name: 'boss-advanced', message: '사용자가 해당 보스를 얼마만에 클리어 하는지 알 수 있는 고급 분석을 하고 싶어', level:'advanced' },
    { name: 'currency-advanced', message: '게임 재화 획득과 소비 원본 로그와 그 로그에서 계산한 일별 요약을 제공하고, 날짜별 순증감 및 소비 편중을 비교하는 고급 분석 문제를 내줘.', level:'advanced' },
    { name: 'purchase-advanced', message: '이용자가 상품 구매에 성공하기까지 얼마만에 걸리는지 고급 분석을 하고 싶어. 최초 시도 시작부터 최초 성공 종료까지 경과 시간으로 정의하고, 실패 시도와 관측 종료까지 구매하지 못한 이용자도 분석하고 싶어.', level:'advanced' }
  ];
  try {
    for (const item of cases) {
      await page.goto(base);
      await page.locator('#request-training').waitFor();
      await page.waitForFunction(()=>typeof document.querySelector('#request-training')?.onclick === 'function');
      await page.locator('#training-request').fill(item.message);
      if(item.level) await page.locator('#request-level').selectOption(item.level);
      let accepted;
      if(resumes[item.name]) {
        accepted={request_id:resumes[item.name]};
      } else {
        const response = page.waitForResponse(r => r.url().endsWith('/api/training/requests') && r.request().method() === 'POST', {timeout:240000});
        await page.locator('#request-training').click();
        accepted = await (await response).json();
      }
      let request;
      for (let i=0;i<240;i++) {
        request = await (await page.request.get(base+'/api/training/requests/'+accepted.request_id)).json();
        if(['ready','failed','needs_clarification','cancelled'].includes(request.status)) break;
        await page.waitForTimeout(1000);
      }
      const record = { name:item.name, request_id:accepted.request_id, status:request.status, error_code:request.error_code, planning_calls:request.planning_calls };
      result.cases.push(record);
      assert.equal(request.status,'ready',JSON.stringify(request));
      if(resumes[item.name]) await page.goto(base+'/?attempt='+request.attempt_id);
      await page.locator('#workspace').waitFor({timeout:15000});
      const attempt = await (await page.request.get(base+'/api/attempts/'+request.attempt_id)).json();
      record.attempt_id=request.attempt_id;
      record.title=attempt.problem.title; record.goal=attempt.problem.goal;
      record.description=attempt.problem.description; record.tables=attempt.problem.required_tables; record.difficulty=attempt.problem.difficulty; assert.equal(record.difficulty,'advanced');
      record.dictionary=attempt.problem.schema;
      assert.equal(attempt.problem.plan_version,'adaptive-plan-v1');
      assert.ok(!attempt.problem.description.includes('新規') && !attempt.problem.description.includes('신규 유저 재방문의 차이'));
      assert.ok(attempt.problem.evaluation_status.includes('시험 과제'));
      if(item.name==='abnormal-users') assert.match(attempt.problem.goal+' '+attempt.problem.description,/비정상|의심|이상|봇/);
      if(item.name==='currency-advanced') assert.match(attempt.problem.goal+attempt.problem.description,/재화|획득|소비/);
      if(item.name==='boss-advanced') assert.match(attempt.problem.goal+attempt.problem.description,/보스/);
      if(item.name==='purchase-advanced') assert.match(attempt.problem.goal+attempt.problem.description,/구매/);
      assert.ok((await page.locator('#schema').textContent()).includes(record.tables[0]));
      await page.locator('[data-tab="sql-workspace"]').click();
      await page.locator('.CodeMirror').click();
      await page.keyboard.insertText(`SELECT count(*) AS row_count FROM "${record.tables[0]}"`);
      const sqlResponse = page.waitForResponse(r=>r.url().endsWith('/execute')&&r.request().method()==='POST');
      await page.locator('#execute').click();
      const execution = await (await sqlResponse).json();
      assert.equal(execution.status,'success');record.sql_rows=execution.rows;
      await page.getByRole('button',{name:'기록에 저장',exact:true}).last().click();
      await page.getByRole('button',{name:'저장 완료',exact:true}).last().waitFor();
      await page.locator('[data-tab="analysis"]').click();
      await page.locator('[data-section="problem_definition"]').fill('요청한 분석 대상과 공개 자료의 집계 단위를 확인한다.');
      await page.locator('[data-section="hypothesis"]').fill('관측 패턴과 정상 반례를 비교하고 원인을 확정하지 않는다.');
      await page.waitForFunction(()=>document.querySelector('#save-status')?.textContent.includes('저장됨'));
      await page.screenshot({path:output+'/'+item.name+'.png',fullPage:true});
      await page.reload(); await page.locator('#workspace').waitFor();
      assert.equal(await page.locator('[data-section="problem_definition"]').inputValue(),'요청한 분석 대상과 공개 자료의 집계 단위를 확인한다.');
      const resumed = await (await page.request.get(base+'/api/attempts/'+request.attempt_id)).json();
      assert.equal(resumed.plan_hash,attempt.plan_hash);assert.equal(resumed.saved_executions.length,attempt.saved_executions.length+1);
      record.saved_resume=true;
      await page.setViewportSize({width:390,height:844});
      record.narrow=await page.evaluate(()=>({width:innerWidth,scrollWidth:document.documentElement.scrollWidth}));
      assert.ok(record.narrow.scrollWidth<=record.narrow.width);
      await page.setViewportSize({width:1280,height:900});
    }
    assert.equal(result.errors.length,0);result.status='pass';
  } catch(e) { result.status='fail';result.failure=e.message;throw e; }
  finally {await fs.writeFile(output+'/result.json',JSON.stringify(result,null,2));await browser.close();}
  console.log(JSON.stringify(result));
}
main().catch(e=>{console.error(e.message);process.exitCode=1;});
