// Actual browser + actual API/model: request-specific data, SQL, save and resume.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs = require('node:fs/promises');
const assert = require('node:assert/strict');
async function main() {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const base = process.env.VERIFY_BASE_URL || 'http://127.0.0.1:8087';
  const output = 'tests/adaptive-browser-2026-10-05';
  await fs.mkdir(output, { recursive: true });
  const result = { actual_browser: true, actual_api: true, actual_model: true, cases: [], errors: [] };
  page.on('pageerror', e => result.errors.push(e.message));
  const cases = [
    { name: 'abnormal-users', message: '게임 비정상 이용자 탐지 및 현상 조사 문제를 내줘. 행동 빈도와 행동 간격의 규칙성으로 의심 계정을 조사하고, 활동량이 많은 정상 이용자와 비교하는 연습을 하고 싶어.', kind: 'investigation' },
    { name: 'currency', message: '게임 재화 획득과 소비 로그로 하루 총 획득량, 소비량과 순증감을 계산하는 초급 문제를 내줘.', kind: 'calculation', level: 'beginner' }
  ];
  try {
    for (const item of cases) {
      await page.goto(base);
      await page.locator('#request-training').waitFor();
      await page.waitForFunction(()=>typeof document.querySelector('#request-training')?.onclick === 'function');
      await page.locator('#training-request').fill(item.message);
      if(item.level) await page.locator('#request-level').selectOption(item.level);
      let accepted;
      if(item.name==='abnormal-users' && process.env.RESUME_BOT_REQUEST) {
        accepted={request_id:process.env.RESUME_BOT_REQUEST};
      } else {
        const response = page.waitForResponse(r => r.url().endsWith('/api/training/requests') && r.request().method() === 'POST', {timeout:240000});
        await page.locator('#request-training').click();
        accepted = await (await response).json();
      }
      let request;
      for (let i=0;i<150;i++) {
        request = await (await page.request.get(base+'/api/training/requests/'+accepted.request_id)).json();
        if(['ready','failed','needs_clarification','cancelled'].includes(request.status)) break;
        await page.waitForTimeout(1000);
      }
      const record = { name:item.name, request_id:accepted.request_id, status:request.status, error_code:request.error_code, planning_calls:request.planning_calls };
      result.cases.push(record);
      assert.equal(request.status,'ready',JSON.stringify(request));
      if(process.env.RESUME_BOT_REQUEST && item.name==='abnormal-users') await page.goto(base+'/?attempt='+request.attempt_id);
      await page.locator('#workspace').waitFor({timeout:15000});
      const attempt = await (await page.request.get(base+'/api/attempts/'+request.attempt_id)).json();
      record.attempt_id=request.attempt_id;
      record.title=attempt.problem.title; record.goal=attempt.problem.goal;
      record.description=attempt.problem.description; record.tables=attempt.problem.required_tables;
      record.dictionary=attempt.problem.schema;
      assert.equal(attempt.problem.plan_version,'adaptive-plan-v1');
      assert.ok(!attempt.problem.description.includes('新規') && !attempt.problem.description.includes('신규 유저 재방문의 차이'));
      assert.ok(attempt.problem.evaluation_status.includes('시험 과제'));
      if(item.name==='abnormal-users') assert.match(attempt.problem.goal+' '+attempt.problem.description,/비정상|의심|이상|봇/);
      if(item.name==='currency') assert.match(attempt.problem.goal+attempt.problem.description,/재화|획득|소비/);
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
