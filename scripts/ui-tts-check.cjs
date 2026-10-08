// Voice management and shared job history checks use synthetic users and responses.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const {mediaTasks} = require('./ui-fixtures.cjs');

(async () => {
  const browser = await chromium.launch({headless: true, channel: 'chrome', args: ['--disable-gpu']});
  try {
    const context = await browser.newContext({viewport: {width: 1440, height: 1000}});
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const now = new Date().toISOString();
    let approved = true, submitted, denyDelete = true;
    const profiles = [{id: 'preset-fixture', owner_id: 'fixture-user', name: '한국어 안내', kind: 'preset',
      speaker: 'Sohee', reference_text: '', requester_id: 'fixture-user', project: 'default', environment: 'production',
      status: 'ready', registration_job_id: null, sample_bytes: 0, error_code: null, created_at: now},
      {id: 'clone-fixture', owner_id: 'fixture-user', name: '등록한 안내 목소리', kind: 'clone', speaker: null,
      reference_text: '안녕하세요. 테스트용 참조 음성입니다.', requester_id: 'fixture-user', project: 'default', environment: 'production',
      status: 'ready', registration_job_id: 'registration-fixture', sample_bytes: 192044, error_code: null, created_at: now},
      {id: 'scoped-preset-fixture', owner_id: 'fixture-user', name: '기본 안내 목소리', kind: 'preset',
      speaker: 'Sohee', reference_text: '', requester_id: 'fixture-user', project: 'demo-project', environment: 'development',
      status: 'ready', registration_job_id: null, sample_bytes: 0, error_code: null, created_at: now}];
    const voices = [];
    const jobs = [];
    const services = ['tts.synthesize', 'tts.voice.register'].map(kind => ({kind, service: 'tts', label: kind.endsWith('register') ? '목소리 등록 · 참조 음성 검증' : '텍스트 음성 생성', input_type: kind.endsWith('register') ? 'upload' : 'text', interface: 'tts', available: true}));
    services.push({kind: 'video.thumbnail', service: 'ffmpeg', label: '영상 썸네일', input_type: 'upload', interface: 'media', available: true});
    const wave = Buffer.alloc(192044);
    wave.write('RIFF'); wave.writeUInt32LE(wave.length - 8, 4); wave.write('WAVEfmt ', 8);
    wave.writeUInt32LE(16, 16); wave.writeUInt16LE(1, 20); wave.writeUInt16LE(1, 22);
    wave.writeUInt32LE(24000, 24); wave.writeUInt32LE(48000, 28); wave.writeUInt16LE(2, 32);
    wave.writeUInt16LE(16, 34); wave.write('data', 36); wave.writeUInt32LE(wave.length - 44, 40);
    await page.context().route('**/api/**', async route => {
      const req = route.request(), url = new URL(req.url()), path = url.pathname;
      let body = [], status = 200;
      if (path === '/api/me') body = {id: 'fixture-user', role: 'admin', status: approved ? 'approved' : 'pending', csrf: 'fixture-csrf'};
      else if (path === '/api/services') body = services;
      else if (path === '/api/tasks') body = mediaTasks(jobs, req.url());
      else if (path === '/api/voices' && req.method() === 'GET') body = voices;
      else if (path === '/api/voices' && req.method() === 'POST') {
        const payload = req.postDataJSON();
        body = {...voices[0], ...payload, id: `new-${voices.length}`, speaker: payload.speaker ?? null, reference_text: payload.reference_text ?? '', status: payload.kind === 'clone' ? 'uploading' : 'ready', registration_job_id: payload.kind === 'clone' ? 'new-registration' : null};
        voices.push(body); status = 201;
      } else if (path === '/api/jobs' && req.method() === 'POST') {
        submitted = req.postDataJSON();
        body = {...submitted, id: `speech-fixture-${jobs.length}`, service: 'tts', stage: 'done', status: 'queued', owner_id: 'fixture-user', worker_id: null, created_at: now, updated_at: now, finished_at: null, expires_at: null, cancel_requested: false, error_code: null, cleanup_state: 'pending', result_state: 'none', result: null};
        jobs.push({...body, status: 'succeeded', worker_id: 'native-worker', finished_at: now, expires_at: new Date(Date.now() + 86400000).toISOString(), result_state: 'available', result: {type: 'artifact', name: 'speech.wav', media_type: 'audio/wav', duration_seconds: 4, peak_memory_bytes: 4719105069, voice_source: submitted.options.voice_id ? 'registered' : 'default', speaker: submitted.options.voice_id ? null : 'Sohee'}}); status = 201;
      } else if (path.endsWith('/input')) {
        voices.find(voice => voice.registration_job_id === 'new-registration').status = 'queued'; body = {status: 'queued'};
      } else if (path.endsWith('/sample') || path.endsWith('/result')) {
        await route.fulfill({status: 200, headers: {'Content-Type': 'audio/wav'}, body: wave}); return;
      } else if (path.startsWith('/api/voices/') && req.method() === 'PATCH') {
        body = voices.find(voice => voice.id === path.split('/').pop()); body.name = req.postDataJSON().name;
      } else if (path.startsWith('/api/voices/') && req.method() === 'DELETE') {
        if (denyDelete) {body = {detail: 'voice_in_use'}; status = 409; denyDelete = false;}
        else {voices.splice(voices.findIndex(voice => voice.id === path.split('/').pop()), 1); body = {deleted: true};}
      }
      await route.fulfill({status, json: body});
    });
    await page.goto('http://127.0.0.1:5173/');
    await page.getByRole('button', {name: '서비스', exact: true}).click();
    await page.locator('.service-list section').filter({has: page.getByRole('heading', {name: '텍스트 음성 생성'})}).getByRole('button', {name: '관리·테스트'}).click();
    await page.getByRole('heading', {name: '등록한 목소리'}).waitFor();
    await page.getByRole('option', {name: '기본 목소리 · Sohee', exact: true}).waitFor({state: 'attached'});
    assert.equal(await page.getByLabel('사용할 목소리', {exact: true}).inputValue(), '');
    await page.getByLabel('말할 내용', {exact: true}).fill('등록하지 않아도 기본 목소리로 말해 주세요.');
    assert.equal(await page.getByRole('button', {name: '음성 생성 작업 등록'}).isEnabled(), true);
    await fs.mkdir('.impeccable/review', {recursive: true});
    await page.screenshot({path: '.impeccable/review/default-empty-desktop.png', fullPage: true});
    const emptyMobile = await context.newPage();
    await emptyMobile.setViewportSize({width: 390, height: 844});
    await emptyMobile.goto('http://127.0.0.1:5173/');
    await emptyMobile.getByRole('button', {name: 'TTS·목소리', exact: true}).click();
    await emptyMobile.getByLabel('말할 내용', {exact: true}).fill('등록하지 않아도 기본 목소리로 말해 주세요.');
    await emptyMobile.evaluate(async () => {await document.fonts.ready; await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))); window.scrollTo(0, 0);});
    await emptyMobile.screenshot({path: '.impeccable/review/default-empty-mobile.png', fullPage: true});
    assert(await emptyMobile.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await emptyMobile.close();
    await page.getByRole('button', {name: '음성 생성 작업 등록'}).click();
    assert.equal(submitted.options.voice_id, null);
    assert.equal(submitted.input.requester_id, 'fixture-user');
    await page.getByRole('button', {name: '기본 목소리 음성 생성', exact: true}).click();
    await page.getByText('기본 목소리 · Sohee', {exact: true}).waitFor();
    await page.waitForFunction(() => document.querySelector('audio[aria-label="생성된 음성"]')?.readyState >= 1);
    await page.screenshot({path: '.impeccable/review/default-job.png', fullPage: true});
    voices.push(...profiles);
    await page.getByRole('button', {name: 'TTS·목소리', exact: true}).click();
    const presetRow = page.getByRole('row').filter({hasText: '기본 · Sohee'}).first();
    await presetRow.getByRole('button', {name: '이름 수정'}).click();
    await page.getByLabel('목소리 이름 수정', {exact: true}).fill('기본 안내 목소리');
    await presetRow.getByRole('button', {name: '저장', exact: true}).click();
    await page.getByRole('cell', {name: '기본 안내 목소리', exact: true}).first().waitFor();
    await page.getByRole('option', {name: '기본 안내 목소리 · fixture-user · default · production', exact: true}).waitFor({state: 'attached'});
    const voiceOptions = page.getByLabel('사용할 목소리', {exact: true}).locator('option');
    assert.equal(await voiceOptions.filter({hasText: '기본 안내 목소리 · fixture-user · default · production'}).count(), 1);
    assert.equal(await voiceOptions.filter({hasText: '기본 안내 목소리 · fixture-user · demo-project · development'}).count(), 1);
    await presetRow.getByRole('button', {name: '삭제', exact: true}).click();
    await page.getByRole('alert').filter({hasText: '사용하는 작업'}).waitFor();
    await page.getByRole('button', {name: '목소리 등록', exact: true}).click();
    await page.getByLabel('목소리 이름', {exact: true}).fill('한국어 두 번째 안내');
    await page.getByRole('button', {name: '목소리 저장'}).click();
    await page.getByRole('cell', {name: '한국어 두 번째 안내', exact: true}).waitFor();
    await page.getByRole('button', {name: '목소리 등록', exact: true}).click();
    await page.getByLabel('목소리 이름', {exact: true}).fill('새 참조 목소리');
    await page.getByLabel('등록 방식', {exact: true}).selectOption('clone');
    await page.getByLabel('참조 음성 파일', {exact: true}).setInputFiles({name: 'sample.wav', mimeType: 'audio/wav', buffer: wave});
    await page.getByLabel('참조 음성의 대본', {exact: true}).fill('안녕하세요. 테스트용 참조 음성입니다.');
    await page.getByRole('button', {name: '목소리 저장'}).click();
    await page.getByText('검증 대기', {exact: true}).waitFor();
    await page.getByLabel('사용할 목소리', {exact: true}).selectOption('clone-fixture');
    assert.equal(await page.getByLabel('말투 지시 (선택)', {exact: true}).count(), 0);
    await page.getByLabel('말할 내용', {exact: true}).fill('등록한 목소리로 문장을 읽어 주세요.');
    await page.getByRole('button', {name: '음성 생성 작업 등록'}).click();
    assert.equal(submitted.options.voice_id, 'clone-fixture');
    assert.equal(submitted.input.requester_id, 'fixture-user');
    await page.getByRole('button', {name: '등록한 안내 목소리 음성 생성', exact: true}).click();
    await page.getByLabel('생성된 음성', {exact: true}).waitFor();
    await page.waitForFunction(() => document.querySelector('audio[aria-label="생성된 음성"]')?.readyState >= 1);
    assert.equal(await page.getByLabel('생성된 음성').evaluate(audio => audio.duration), 4);
    assert.match(await page.getByLabel('생성된 음성').getAttribute('src'), /speech-fixture-1\/result/);
    await fs.mkdir('.impeccable/review', {recursive: true});
    await page.screenshot({path: '.impeccable/review/tts-job.png', fullPage: true});
    await page.getByRole('button', {name: 'TTS·목소리', exact: true}).click();
    await page.getByRole('button', {name: '목소리 등록', exact: true}).click();
    await page.getByLabel('등록 방식', {exact: true}).selectOption('clone');
    await page.getByLabel('목소리 이름', {exact: true}).fill('새로운 안내 목소리');
    await page.getByLabel('말할 내용', {exact: true}).fill('오늘도 좋은 하루 보내세요.');
    await page.screenshot({path: '.impeccable/review/desktop.png', fullPage: true});
    // Start at the target width; resizing a long Chrome page can corrupt its capture.
    const mobile = await page.context().newPage();
    await mobile.setViewportSize({width: 390, height: 844});
    mobile.on('pageerror', error => errors.push(error.message));
    await mobile.goto('http://127.0.0.1:5173/');
    await mobile.getByRole('button', {name: 'TTS·목소리', exact: true}).click();
    await mobile.getByRole('button', {name: '목소리 등록', exact: true}).click();
    await mobile.getByLabel('등록 방식', {exact: true}).selectOption('clone');
    await mobile.getByLabel('목소리 이름', {exact: true}).fill('새로운 안내 목소리');
    await mobile.getByLabel('말할 내용', {exact: true}).fill('오늘도 좋은 하루 보내세요.');
    await mobile.evaluate(async () => {await document.fonts.ready; await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))); window.scrollTo(0, 0);});
    await mobile.screenshot({path: '.impeccable/review/mobile.png', fullPage: true});
    assert(await mobile.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'page must fit mobile viewport');
    approved = false;
    await page.reload();
    await page.getByText('승인을 기다리고 있습니다.', {exact: false}).waitFor();
    assert.equal(await page.getByRole('button', {name: '목소리 등록', exact: true}).count(), 0);
    assert.deepEqual(errors, []);
    console.log('TTS UI: default speech without profiles, default result label, preset/clone registration, rename, scope, active deletion rejection, selected synthesis, shared history, playback, mobile, approval gates passed');
  } finally {await browser.close();}
})().catch(error => {console.error(error); process.exitCode = 1;});
