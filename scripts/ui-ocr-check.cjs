// OCR UI checks use synthetic users, images, and recognized lines; no production auth bypass.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const {mediaTasks} = require('./ui-fixtures.cjs');

(async () => {
  const browser = await chromium.launch({headless: true, channel: 'chrome', args: ['--disable-gpu']});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const now = new Date().toISOString(), future = new Date(Date.now() + 86400000).toISOString();
    let payload, uploaded = false, approved = true, failResult = true, cancelled, silent = false;
    const service = {kind: 'ocr.recognize', service: 'ocr', label: '이미지 문자 인식 · 텍스트 + 줄별 위치',
      interface: 'media', input_type: 'upload', available: true,
      ocr_limits: {max_input_bytes: 32000000, max_pixels: 40000000, max_dimension: 10000, processing_dimension: 4096, timeout_seconds: 120}};
    const base = {kind: service.kind, service: 'ocr', options: {language: 'auto', language_correction: true},
      owner_id: 'fixture-user', worker_id: null, created_at: now, updated_at: now,
      finished_at: null, expires_at: null, cancel_requested: false, error_code: null,
      cleanup_state: 'pending', result_state: 'none', result: null, status: 'queued', stage: 'waiting_compute'};
    const complete = {...base, id: 'completed-ocr-fixture', title: '가상 문서 문자 인식', status: 'succeeded',
      stage: 'done', worker_id: 'native-worker', finished_at: now, expires_at: future,
      result_state: 'available', result: {type: 'ocr_recognize', line_count: 52, files: ['text.json', 'text.txt', 'text.zip']}};
    const jobs = [complete];
    await page.route('**/api/**', async route => {
      const req = route.request(), path = new URL(req.url()).pathname;
      let body = [], status = 200;
      if (path === '/api/me') body = {id: 'fixture-user', role: 'admin', status: approved ? 'approved' : 'pending', csrf: 'fixture-csrf'};
      else if (path === '/api/services') body = [service];
      else if (path === '/api/tasks') body = mediaTasks(jobs, req.url());
      else if (path === '/api/compute') body = {concurrency: 1, requests: []};
      else if (path === '/api/jobs' && req.method() === 'POST') {
        payload = req.postDataJSON(); body = {...base, ...payload, id: 'new-ocr-fixture', status: 'uploading'};
        jobs.unshift(body); status = 201;
      } else if (path.endsWith('/input')) {
        uploaded = true; jobs[0].status = 'queued'; body = {status: 'queued'};
      } else if (path.endsWith('/cancel')) {
        cancelled = path; jobs[0].status = 'cancelled'; body = {accepted: true};
      } else if (path.endsWith('/files/text.json')) {
        if (failResult) {failResult = false; status = 503; body = {detail: 'result_missing'};}
        else body = {text: silent ? '' : '가상 결과입니다.', lines: silent ? [] : Array.from({length: 52}, (_, index) => ({confidence: 0.97, text: `줄 ${index + 1} · 가상 테스트 결과입니다. 실제 문자 인식 결과와 구분합니다. ${index === 0 ? 'LongToken'.repeat(20) : ''}`}))};
      }
      await route.fulfill({status, json: body});
    });
    await page.goto('http://127.0.0.1:5173/');
    await page.getByRole('button', {name: '서비스', exact: true}).click();
    await page.getByRole('heading', {name: service.label, exact: true}).waitFor();
    await page.getByRole('button', {name: '작업 만들기', exact: true}).click();
    await page.getByLabel('작업명', {exact: true}).fill('새 문자 인식 테스트');
    await page.getByLabel('인식 언어', {exact: true}).selectOption('ko');
    await page.getByLabel('언어 보정', {exact: true}).selectOption('off');
    await page.getByLabel('입력 이미지', {exact: true}).setInputFiles({name: 'fixture.png', mimeType: 'image/png', buffer: Buffer.from('synthetic UI file')});
    await fs.mkdir('.impeccable/review', {recursive: true});
    for (const [name, width] of [['desktop', 1440], ['mobile', 390]]) {
      await page.setViewportSize({width, height: 1000});
      await page.evaluate(() => scrollTo(0, 0));
      await page.evaluate(() => document.fonts.ready);
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      await page.screenshot({animations: 'disabled', path: `.impeccable/review/ocr-form-${name}.png`, fullPage: true});
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    }
    await page.getByRole('button', {name: '작업 시작', exact: true}).click();
    await page.getByRole('button', {name: '새 문자 인식 테스트', exact: true}).waitFor();
    assert.equal(uploaded, true);
    assert.equal(payload.kind, service.kind);
    assert.deepEqual(payload.options, {language: 'ko', language_correction: false});
    await page.getByRole('button', {name: '새 문자 인식 테스트', exact: true}).click();
    await page.getByRole('button', {name: '작업 취소', exact: true}).click();
    assert.match(cancelled, /new-ocr-fixture\/cancel$/);
    await page.getByRole('button', {name: '가상 문서 문자 인식', exact: true}).click();
    await page.getByRole('button', {name: '결과 다시 불러오기', exact: true}).waitFor();
    await page.getByRole('button', {name: '결과 다시 불러오기', exact: true}).click();
    await page.getByLabel('문자 인식 결과', {exact: true}).waitFor();
    assert.equal(await page.locator('.ocr-lines li').count(), 50);
    assert.match(await page.getByRole('link', {name: '텍스트 다운로드', exact: true}).getAttribute('href'), /files\/text.txt$/);
    assert.match(await page.getByRole('link', {name: '줄별 JSON 다운로드', exact: true}).getAttribute('href'), /files\/text.json$/);
    for (const [name, width] of [['desktop', 1440], ['mobile', 390]]) {
      await page.setViewportSize({width, height: 1000});
      await page.evaluate(() => scrollTo(0, 0));
      await page.evaluate(() => document.fonts.ready);
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      await page.screenshot({animations: 'disabled', path: `.impeccable/review/ocr-result-${name}.png`, fullPage: true});
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    }
    await page.getByRole('button', {name: '다음 줄', exact: true}).click();
    assert.equal(await page.locator('.ocr-lines li').count(), 2);
    assert.equal(await page.getByRole('button', {name: '다음 줄', exact: true}).isDisabled(), true);
    silent = true;
    await page.getByRole('button', {name: '가상 문서 문자 인식', exact: true}).click();
    await page.getByRole('button', {name: '가상 문서 문자 인식', exact: true}).click();
    await page.getByText('인식된 문자가 없습니다. 해상도·대비와 입력 내용을 확인해 주세요.', {exact: true}).waitFor();
    complete.expires_at = new Date(Date.now() - 1000).toISOString(); complete.result_state = 'expired';
    await page.reload();
    await page.getByRole('button', {name: '가상 문서 문자 인식', exact: true}).click();
    await page.getByText('보관 기간이 만료되었습니다.', {exact: true}).waitFor();
    assert.equal(await page.getByRole('link', {name: '텍스트 다운로드', exact: true}).count(), 0);
    approved = false;
    await page.reload();
    await page.getByText('관리자 승인을 기다리고 있습니다.', {exact: true}).waitFor();
    assert.equal(await page.getByRole('button', {name: '새 작업', exact: true}).count(), 0);
    assert.deepEqual(errors, []);
    console.log('OCR service, upload/options, common job/cancel, result recovery/pagination/download, empty/expiry/auth and responsive checks passed');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
