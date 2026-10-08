// Offline fixtures verify resource waiting/recovery without n8n, platform or registries.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const {mediaTasks} = require('./ui-fixtures.cjs');

(async () => {
  const browser = await chromium.launch({headless: true, channel: 'chrome', args: ['--disable-gpu']});
  const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  const now = new Date().toISOString();
  let admin = true;
  let blocked = false;
  let liveChild = true;
  let posted;
  const job = {id: 'fixture-queued-job', kind: 'tts.synthesize', service: 'tts', title: '다음 목소리 생성',
    owner_id: 'fixture-user', status: 'running', stage: 'waiting_compute', worker_id: 'fixture-worker',
    created_at: now, updated_at: now, finished_at: null, expires_at: null, cancel_requested: false,
    result_state: 'none', cleanup_state: 'pending', error_code: null, result: null};
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    let body = [];
    if (path === '/api/me') body = {id: 'fixture-user', role: admin ? 'admin' : 'user', status: 'approved', csrf: 'fixture-csrf'};
    else if (path === '/api/compute') body = {concurrency: 1, requests: [
      {id: 'fixture-reservation', kind: 'ai.workflow', job_id: 'fixture-parent-job', parent_id: null,
        state: blocked ? 'interrupted' : 'running'},
      {id: 'fixture-queued', kind: 'tts.synthesize', job_id: job.id, parent_id: null, state: 'queued'},
    ]};
    else if (path === '/api/tasks') body = mediaTasks([job], route.request().url()).map(task => ({...task,
      status: 'queued', compute: {state: 'queued', parent_id: null, error_code: null}}));
    else if (path === '/api/services') body = [
      {kind: 'tts.synthesize', service: 'tts', label: '음성 생성', input_type: 'text', interface: 'tts', available: true},
      {kind: 'ai.workflow', service: 'n8n', label: 'AI 작업 · n8n', input_type: 'text', interface: 'ai', available: false,
        unavailable_reason: 'n8n의 공통 자원 헤더 연결·검수가 필요합니다.'},
    ];
    else if (path.endsWith('/acknowledge-stopped')) {
      posted = route.request().postDataJSON();
      assert.equal(route.request().headers()['x-csrf-token'], 'fixture-csrf');
      if (liveChild) return route.fulfill({status: 409, json: {detail: 'compute_process_still_running'}});
      blocked = false; body = {released: true};
    } else if (path === '/api/admin/raya/status') body = {state: 'off', execution_policy: 'per_job', error_code: null,
      waiting: 0, minimum_keep_seconds: 60, idle_seconds: 300, wait_seconds: 5,
      compute_wait_seconds: 600, timeout_seconds: 90, memory_reserve_bytes: 3221225472};
    await route.fulfill({json: body});
  });
  await page.goto('http://127.0.0.1:5173/');
  await page.getByRole('heading', {name: '공통 실행 자원'}).waitFor();
  await page.getByRole('button', {name: job.title, exact: true}).click();
  await page.getByText('공통 실행 자원 배정을 기다리고 있습니다.', {exact: true}).waitFor();
  assert.equal(await page.locator('.jobs-table .status.queued').count(), 1);
  assert.equal((await page.locator('.steps .current').innerText()).trim(), '대기');
  await fs.mkdir('.impeccable/review', {recursive: true});
  await page.screenshot({path: '.impeccable/review/compute-desktop.png', fullPage: true});
  blocked = true;
  await page.reload();
  await page.getByRole('button', {name: '실행 종료 확인', exact: true}).click();
  await page.getByRole('button', {name: '종료 확인 후 예약 해제', exact: true}).click();
  await page.getByRole('alert').waitFor();
  assert.ok((await page.getByRole('alert').innerText()).includes('로컬 연산 프로세스가 아직 실행 중입니다. 종료 후 다시 확인해 주세요.'));
  assert.deepEqual(posted, {execution_stopped: true});
  await page.screenshot({path: '.impeccable/review/compute-desktop-blocked.png', fullPage: true});
  await page.setViewportSize({width: 390, height: 844});
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  assert.equal(await page.locator('header').count(), 1);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await page.screenshot({path: '.impeccable/review/compute-mobile-blocked.png', fullPage: true});
  liveChild = false;
  await page.getByRole('button', {name: '종료 확인 후 예약 해제', exact: true}).click();
  await page.waitForFunction(() => !document.querySelector('.compute-panel .danger'));
  admin = false; blocked = true;
  await page.reload();
  await page.getByText('이전 실행의 종료 여부를 확인해야 합니다. 새 연산은 자원을 배정받을 때까지 기다립니다.', {exact: true}).waitFor();
  assert.equal(await page.getByRole('button', {name: '실행 종료 확인', exact: true}).count(), 0);
  admin = true; blocked = false;
  await page.reload();
  await page.getByRole('navigation').getByRole('button', {name: '서비스', exact: true}).click();
  await page.getByText('n8n의 공통 자원 헤더 연결·검수가 필요합니다.', {exact: true}).waitFor();
  await page.getByRole('navigation').getByRole('button', {name: 'Raya', exact: true}).click();
  await page.getByText('현재는 작업마다 모델을 해제합니다.', {exact: false}).waitFor();
  assert.equal(await page.getByRole('button', {name: '실행 정책 저장'}).count(), 0);
  assert.ok((await page.locator('.raya-panel').innerText()).includes('자원 배정 대기 600초'));
  await page.screenshot({path: '.impeccable/review/compute-raya-mobile.png', fullPage: true});
  await page.setViewportSize({width: 1440, height: 1000});
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  assert.equal(await page.locator('header').count(), 1);
  await page.screenshot({path: '.impeccable/review/compute-raya-desktop.png', fullPage: true});
  assert.deepEqual(errors, []);
  await browser.close();
  console.log('Resource UI passed: shared wait state, interrupted execution, active-child rejection, confirmed release, role limits, mobile layout and n8n readiness. Offline fixtures.');
})().catch(error => {console.error(error); process.exit(1);});
