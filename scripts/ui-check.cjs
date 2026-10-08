const { mediaTasks } = require('./ui-fixtures.cjs');
// Browser checks use explicitly synthetic API fixtures, never production identity or keys.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');

(async () => {
  const browser = await chromium.launch({ headless: true, channel: 'chrome', args: ['--disable-gpu'] });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  let currentStatus = 'approved';
  let cancelled = false;
  let uploadAttempts = 0;
  const requests = [];
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const created = new Date().toISOString();
  const kinds = ['tts.synthesize', 'embedding.encode', 'video.thumbnail', 'stt.transcribe', 'batch.run'];
  const titles = ['안내 문구 음성 합성', '문서 검색 벡터 생성', '미리보기 이미지 추출', '회의 녹음 받아쓰기', '일일 데이터 정리'];
  const states = ['running', 'queued', 'succeeded', 'failed', 'succeeded'];
  const jobs = kinds.map((kind, i) => ({
    id: `00000000-0000-4000-8000-00000000000${i}`, title: titles[i], kind, service: kind.split('.')[0],
    status: states[i], stage: i < 2 ? 'executing' : 'finished', owner_id: 'fixture-user', worker_id: i === 1 ? null : 'fixture-worker',
    created_at: created, updated_at: created, finished_at: i < 2 ? null : created, expires_at: null,
    cancel_requested: false, error_code: i === 3 ? 'unsupported_media' : null, cleanup_state: 'done', result_state: 'none',
  }));
  await page.route('**/api/**', async route => {
    const pathname = new URL(route.request().url()).pathname;
    let body;
    if (pathname === '/api/me') body = { id: 'fixture-user', role: 'admin', status: currentStatus, csrf: 'fixture-csrf' };
    else if (pathname === '/api/jobs' && route.request().method() === 'POST') {
      requests.push(route.request().postDataJSON());
      body = { ...jobs[0], status: 'uploading' };
    }
    else if (pathname.endsWith('/input')) {
      uploadAttempts += 1;
      await route.fulfill({ status: uploadAttempts === 1 ? 503 : 200, json: { detail: 'upload_failed', status: 'queued' } });
      return;
    }
    else if (pathname === '/api/tasks') body = mediaTasks(jobs, route.request().url());
    else if (pathname === '/api/services') body = [{ kind: 'video.thumbnail', service: 'ffmpeg', label: '영상 썸네일', input_type: 'upload' }];
    else if (pathname === '/api/admin/users') body = [];
    else if (pathname === '/api/admin/workers') body = [];
    else if (pathname.endsWith('/cancel')) { cancelled = true; body = { status: 'cancellation_requested' }; }
    else { await route.fulfill({ status: 404, json: { detail: 'fixture_missing' } }); return; }
    await route.fulfill({ json: body });
  });
  await page.goto('http://127.0.0.1:5173/');
  await page.getByRole('button', { name: titles[0], exact: true }).click();
  assert.equal(await page.getByRole('table').count(), 1);
  await page.getByRole('button', {name: titles[1], exact:true}).click();
  assert.equal(await page.locator('.steps li.current').innerText(), '대기');
  await page.getByRole('button', {name: titles[0], exact:true}).click();
  await page.getByRole('button', { name: '작업 취소', exact: true }).click();
  assert.ok(cancelled);
  await fs.mkdir('.impeccable/review', { recursive: true });
  await page.evaluate(() => {
    document.querySelector('.toolbar p').textContent += ' · UI 검증 예시 데이터';
  });
  await page.setViewportSize({ width: 1505, height: 1045 });
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.waitForTimeout(300);
  await page.screenshot({ path: '.impeccable/review/hero-repro.png', fullPage: false });
  await page.setViewportSize({ width: 1440, height: 1300 });
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.waitForTimeout(300);
  await page.screenshot({ path: '.impeccable/review/desktop.png', fullPage: false });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  const height = await page.evaluate(() => document.documentElement.scrollHeight);
  await page.setViewportSize({ width: 390, height });
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.waitForTimeout(300);
  await page.screenshot({ path: '.impeccable/review/mobile.png', fullPage: false });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole('button', { name: '새 작업', exact: true }).click();
  await page.getByLabel('작업명', { exact: true }).fill('테스트');
  assert.equal(await page.getByRole('combobox', { name: /작업 종류/ }).inputValue(), 'video.thumbnail');
  await page.getByLabel('입력 영상', { exact: true }).setInputFiles({name:'fixture.mp4',mimeType:'video/mp4',buffer:Buffer.from('fixture')});
  await page.getByRole('button', {name:'작업 시작',exact:true}).click();
  await page.getByRole('alert').waitFor();
  assert.ok(await page.getByLabel('작업명', {exact:true}).isDisabled());
  await page.getByRole('button', {name:'같은 입력으로 다시 시도',exact:true}).click();
  await page.getByRole('heading', {name:'새 작업',exact:true}).waitFor({state:'hidden'});
  assert.equal(uploadAttempts, 2);
  assert.deepEqual(requests[0], requests[1]);
  currentStatus = 'pending';
  await page.reload();
  await page.getByRole('heading', { name: '관리자 승인을 기다리고 있습니다.' }).waitFor();
  assert.equal(await page.getByRole('table').count(), 0);
  assert.deepEqual(errors, []);
  await browser.close();
  console.log('UI checks passed: expand, cancel, creation form, approval gate, mobile overflow; synthetic fixtures.');
})().catch(error => { console.error(error); process.exit(1); });
