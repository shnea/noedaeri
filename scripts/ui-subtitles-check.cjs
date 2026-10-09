// Subtitles UI checks use synthetic users, audio, and transcripts; no production auth bypass.
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
    const service = {kind: 'video.subtitles', service: 'subtitles', label: '영상 자막 · SRT + VTT',
      interface: 'media', input_type: 'upload', available: true,
      limits: {max_duration_seconds: 3600, timeout_seconds: 900, cpu_threads: 4}};
    const base = {kind: service.kind, service: 'subtitles', options: {language: 'auto', use_itn: true},
      owner_id: 'fixture-user', worker_id: null, created_at: now, updated_at: now,
      finished_at: null, expires_at: null, cancel_requested: false, error_code: null,
      cleanup_state: 'pending', result_state: 'none', result: null, status: 'queued', stage: 'waiting_compute'};
    const complete = {...base, id: 'completed-stt-fixture', title: '가상 영상 자막', status: 'succeeded',
      stage: 'done', worker_id: 'native-worker', finished_at: now, expires_at: future,
      result_state: 'available', result: {type: 'video_subtitles', segment_count: 52,
        duration_seconds: 300, files: ['transcript.json', 'transcript.txt', 'subtitles.srt', 'subtitles.vtt', 'subtitles.zip']}};
    const jobs = [complete];
    await page.route('**/api/**', async route => {
      const req = route.request(), path = new URL(req.url()).pathname;
      let body = [], status = 200;
      if (path === '/api/me') body = {id: 'fixture-user', role: 'admin', status: approved ? 'approved' : 'pending', csrf: 'fixture-csrf'};
      else if (path === '/api/services') body = [service];
      else if (path === '/api/tasks') body = mediaTasks(jobs, req.url());
      else if (path === '/api/compute') body = {concurrency: 1, requests: []};
      else if (path === '/api/jobs' && req.method() === 'POST') {
        payload = req.postDataJSON(); body = {...base, ...payload, id: 'new-stt-fixture', status: 'uploading'};
        jobs.unshift(body); status = 201;
      } else if (path.endsWith('/input')) {
        uploaded = true; jobs[0].status = 'queued'; body = {status: 'queued'};
      } else if (path.endsWith('/cancel')) {
        cancelled = path; jobs[0].status = 'cancelled'; body = {accepted: true};
      } else if (path.endsWith('/files/transcript.json')) {
        if (failResult) {failResult = false; status = 503; body = {detail: 'result_missing'};}
        else body = {text: silent ? '' : '가상 결과입니다.', duration_seconds: 300,
          segments: silent ? [] : Array.from({length: 52}, (_, index) => ({start: index === 0 ? 59.968 : index === 1 ? 119.968 : 120 + index * 3, end: index === 0 ? 62 : index === 1 ? 122 : 122 + index * 3,
            text: `구간 ${index + 1} · 이 화면은 가상 테스트 결과입니다. 실제 음성 인식 결과와 구분합니다.`}))};
      }
      await route.fulfill({status, json: body});
    });
    await page.goto('http://127.0.0.1:5173/');
    await page.getByRole('button', {name: '서비스', exact: true}).click();
    await page.getByRole('heading', {name: service.label, exact: true}).waitFor();
    await page.getByRole('button', {name: '작업 만들기', exact: true}).click();
    await page.getByLabel('작업명', {exact: true}).fill('새 자막 테스트');
    await page.getByLabel('인식 언어', {exact: true}).selectOption('ko');
    await page.getByLabel('숫자·문장 표기', {exact: true}).selectOption('off');
    await page.getByLabel('입력 음성·영상', {exact: true}).setInputFiles({name: 'fixture.wav', mimeType: 'audio/wav', buffer: Buffer.from('synthetic UI file')});
    await fs.mkdir('.impeccable/review', {recursive: true});
    for (const [name, width] of [['desktop', 1440], ['mobile', 390]]) {
      await page.setViewportSize({width, height: 1000});
      await page.evaluate(() => scrollTo(0, 0));
      await page.evaluate(() => document.fonts.ready);
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      await page.screenshot({animations: 'disabled', path: `.impeccable/review/subtitles-form-${name}.png`, fullPage: true});
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    }
    await page.getByRole('button', {name: '작업 시작', exact: true}).click();
    await page.getByRole('button', {name: '새 자막 테스트', exact: true}).waitFor();
    assert.equal(uploaded, true);
    assert.equal(payload.kind, service.kind);
    assert.deepEqual(payload.options, {language: 'ko', use_itn: false});
    await page.getByRole('button', {name: '새 자막 테스트', exact: true}).click();
    await page.getByRole('button', {name: '작업 취소', exact: true}).click();
    assert.match(cancelled, /new-stt-fixture\/cancel$/);
    await page.getByRole('button', {name: '가상 영상 자막', exact: true}).click();
    await page.getByRole('button', {name: '결과 다시 불러오기', exact: true}).waitFor();
    await page.getByRole('button', {name: '결과 다시 불러오기', exact: true}).click();
    await page.getByLabel('음성 인식 결과', {exact: true}).waitFor();
    assert.equal(await page.locator('.transcript-segments p').count(), 50);
    const region=page.getByLabel('음성 인식 결과', {exact:true});
    await region.focus();await page.keyboard.press('ArrowDown');await page.waitForTimeout(180);
    assert.ok(await region.evaluate(el=>el.scrollTop>0));assert.equal(await region.evaluate(el=>getComputedStyle(el).outlineWidth),'3px');
    await region.evaluate(el=>{el.scrollTop=0});
    assert.equal(await page.locator('.segment-time').filter({hasText: '1:00.0–1:02.0'}).count(), 1);
    assert.equal(await page.locator('.segment-time').filter({hasText: '2:00.0–2:02.0'}).count(), 1);
    assert.match(await page.getByRole('link', {name: '텍스트 다운로드', exact: true}).getAttribute('href'), /files\/transcript.txt$/);
    assert.match(await page.getByRole('link', {name: 'SRT 자막 다운로드', exact: true}).getAttribute('href'), /files\/subtitles.srt$/);
    assert.match(await page.getByRole('link', {name: 'VTT 자막 다운로드', exact: true}).getAttribute('href'), /files\/subtitles.vtt$/);
    await page.getByText(/자막 시각은 음성 구간을 문자 수에 비례해 나눈 근사값/).waitFor();
    assert.match(await page.getByRole('link', {name: '구간 JSON 다운로드', exact: true}).getAttribute('href'), /files\/transcript.json$/);
    for (const [name, width] of [['desktop', 1440], ['mobile', 390]]) {
      await page.setViewportSize({width, height: 1000});
      await page.evaluate(() => scrollTo(0, 0));
      await page.evaluate(() => document.fonts.ready);
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      await page.screenshot({animations: 'disabled', path: `.impeccable/review/subtitles-result-${name}.png`, fullPage: true});
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    }
    await page.getByRole('button', {name: '다음 구간', exact: true}).click();
    assert.equal(await page.locator('.transcript-segments p').count(), 2);
    assert.equal(await page.getByRole('button', {name: '다음 구간', exact: true}).isDisabled(), true);
    silent = true;
    await page.getByRole('button', {name: '가상 영상 자막', exact: true}).click();
    await page.getByRole('button', {name: '가상 영상 자막', exact: true}).click();
    await page.getByText('인식된 음성이 없습니다. 무음 또는 입력 상태를 확인해 주세요.', {exact: true}).waitFor();
    complete.expires_at = new Date(Date.now() - 1000).toISOString(); complete.result_state = 'expired';
    await page.reload();
    await page.getByRole('button', {name: '가상 영상 자막', exact: true}).click();
    await page.getByText('보관 기간이 만료되었습니다.', {exact: true}).waitFor();
    assert.equal(await page.getByRole('link', {name: '텍스트 다운로드', exact: true}).count(), 0);
    approved = false;
    await page.reload();
    await page.getByText('관리자 승인을 기다리고 있습니다.', {exact: true}).waitFor();
    assert.equal(await page.getByRole('button', {name: '새 작업', exact: true}).count(), 0);
    assert.deepEqual(errors, []);
    console.log('Subtitles service, upload/options, common job/cancel, result recovery/pagination/download, silence/expiry/auth and responsive checks passed');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
