// Combined work history and navigation checks use synthetic identities and responses only.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const { mediaTasks } = require('./ui-fixtures.cjs');

(async () => {
  const browser = await chromium.launch({ headless: true, channel: 'chrome', args: ['--disable-gpu'] });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const now = new Date().toISOString();
  const future = new Date(Date.now() + 86400000).toISOString();
  let approved = true;
  let testingPagination = false;
  let createdPayload;
  let cancelPath;
  const media = { id: 'media-fixture', title: '영상 미리보기', kind: 'video.thumbnail', service: 'ffmpeg',
    status: 'queued', stage: 'waiting', owner_id: 'fixture-user', worker_id: null, created_at: now,
    updated_at: now, finished_at: null, expires_at: null, cancel_requested: false,
    error_code: null, cleanup_state: 'pending', result_state: 'none', result: null };
  const base = {owner_id: 'fixture-user', origin: 'web', created_at: now};
  const tasks = [mediaTasks([media], 'http://fixture.example/')[0], {
    ...base, source: 'ai', id: 'ai-fixture', title: '블로그 요약', kind: 'ai.workflow', service: 'n8n',
    label: '블로그 요약', status: 'succeeded', executor: 'n8n', data: {
      id: 'ai-fixture', request_id: 'fixture-ai-request', task_type: 'blog.summary', project: 'fixture',
      environment: 'test', status: 'succeeded', result: {ai_result: '합쳐진 작업 목록의 AI 결과'},
      error_code: null, error_message: null, created_at: now, finished_at: now, expires_at: future,
    }
  }, {
    ...base, source: 'indexing', id: 'index-fixture', title: '문서 추가·갱신 · portfolio', kind: 'indexing.documents',
    service: 'indexing', label: '문서 추가·갱신', status: 'queued', executor: '색인 처리기', data: {
      id: 'index-fixture', owner_id: 'fixture-user', result_state: 'none', request_id: 'fixture-index-request',
      project: 'fixture', environment: 'test', collection: 'portfolio', mode: 'upsert', status: 'pending',
      document_count: 2, indexed_count: 0, deleted_count: 0, total_tokens: 0, result: null,
      error_code: null, error_message: null, created_at: now, finished_at: null, expires_at: null,
    }
  }, {
    ...base, owner_id: 'other-user-fixture', source: 'operation', id: 'embedding-fixture', title: '텍스트 임베딩 생성', kind: 'embedding.encode',
    service: 'embedding', label: '텍스트 임베딩 생성', status: 'succeeded', executor: '직접 호출',
    data: { finished_at: now, expires_at: future, error_code: null,
      result: {model: 'fixture-model', dimensions: 768, vector_count: 1} },
  }];
  const services = [
    {kind: 'video.thumbnail', service: 'ffmpeg', label: '영상 썸네일', interface: 'media', input_type: 'upload', available: true},
    {kind: 'ai.workflow', service: 'n8n', label: 'AI 작업 · n8n 워크플로', interface: 'ai', input_type: 'text', available: true,
      task_types: [{value: 'blog.summary', label: '블로그 요약'}, {value: 'chat.general', label: '일반 질답'}]},
    {kind: 'embedding.encode', service: 'embedding', label: '텍스트 임베딩 생성', interface: 'embedding', input_type: 'text', available: false},
  ];
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    let body = [];
    if (path === '/api/me') body = {id: 'fixture-user', role: 'admin', status: approved ? 'approved' : 'pending', csrf: 'fixture-csrf'};
    else if (path === '/api/tasks' && testingPagination) body = Number(url.searchParams.get('offset')) > 0
      ? [] : Array.from({length: 100}, (_, index) => ({...tasks[0], id: `page-fixture-${index}`}));
    else if (path === '/api/tasks') body = tasks.filter(task =>
      (!url.searchParams.has('service') || task.service === url.searchParams.get('service')) &&
      (!url.searchParams.has('status') || task.status === url.searchParams.get('status')));
    else if (path === '/api/services') body = services;
    else if (path === '/api/ai/usage') body = {summary: [], records: []};
    else if (path === '/api/ai/jobs') {
      createdPayload = route.request().postDataJSON();
      body = {...tasks[1].data, id: 'new-ai-fixture', request_id: createdPayload.request_id, status: 'running'};
      tasks.push({...tasks[1], id: body.id, status: 'running', data: body});
    } else if (path.endsWith('/cancel')) {
      cancelPath = path;
      tasks[2].status = 'cancelled'; tasks[2].data.status = 'cancelled'; body = tasks[2].data;
    }
    await route.fulfill({json: body});
  });
  await page.goto('http://127.0.0.1:5173/');
  await page.getByRole('button', {name: '영상 미리보기', exact: true}).waitFor();
  assert.equal(await page.locator('.jobs-table tbody > tr').count(), 4);
  assert.equal(await page.getByRole('navigation').getByRole('button', {name: 'AI 작업', exact: true}).count(), 0);
  await page.getByRole('button', {name: '블로그 요약', exact: true}).click();
  assert.ok((await page.locator('.json-result').innerText()).includes('합쳐진 작업 목록'));
  await page.getByLabel('서비스', {exact: true}).selectOption('n8n');
  await page.waitForFunction(() => document.querySelectorAll('.jobs-table tbody > tr:not(.detail-row)').length === 1);
  await page.getByLabel('서비스', {exact: true}).selectOption('all');
  await page.getByLabel('상태', {exact: true}).selectOption('failed');
  await page.getByRole('heading', {name: '조건에 맞는 작업이 없습니다.', exact: true}).waitFor();
  assert.equal(await page.getByRole('button', {name: '첫 작업 만들기', exact: true}).count(), 0);
  await fs.mkdir('.impeccable/review', {recursive: true});
  await page.screenshot({path: '.impeccable/review/tasks-empty-filter.png', fullPage: false});
  await page.getByRole('button', {name: '필터 초기화', exact: true}).click();
  await page.getByRole('button', {name: '문서 추가·갱신 · portfolio', exact: true}).waitFor();
  await page.getByRole('button', {name: '문서 추가·갱신 · portfolio', exact: true}).click();
  await page.getByRole('button', {name: '작업 취소', exact: true}).click();
  assert.equal(cancelPath, '/api/ai/indexing/index-fixture/cancel');
  await page.getByRole('navigation').getByRole('button', {name: '서비스', exact: true}).click();
  await fs.mkdir('.impeccable/review', {recursive: true});
  await page.screenshot({path: '.impeccable/review/tasks-services.png', fullPage: false});
  const ai = page.locator('.service-list section').filter({has: page.getByRole('heading', {name: 'AI 작업 · n8n 워크플로'})});
  await ai.getByRole('button', {name: '작업 만들기', exact: true}).click();
  assert.equal(await page.getByLabel('작업 종류', {exact: true}).inputValue(), 'ai.workflow');
  await page.getByLabel('AI 작업 유형', {exact: true}).selectOption('blog.summary');
  await page.getByLabel('입력 내용', {exact: true}).fill('검증용 요약 입력');
  await page.getByRole('button', {name: '작업 시작', exact: true}).click();
  await page.getByRole('heading', {name: '새 작업', exact: true}).waitFor({state: 'hidden'});
  assert.equal(createdPayload.sync, false);
  assert.equal(createdPayload.task_type, 'blog.summary');
  await page.getByRole('navigation').getByRole('button', {name: '사용량', exact: true}).click();
  await page.getByRole('heading', {name: '모델별 토큰 사용량'}).waitFor();
  assert.equal(await page.locator('.jobs-table').count(), 0);
  await page.getByRole('navigation').getByRole('button', {name: '서비스', exact: true}).click();
  const embedding = page.locator('.service-list section').filter({has: page.getByRole('heading', {name: '텍스트 임베딩 생성', exact: true})});
  assert.ok(await embedding.getByRole('button', {name: '관리·테스트'}).isDisabled());
  await page.getByRole('navigation').getByRole('button', {name: '작업', exact: true}).click();
  await page.getByRole('button', {name: '텍스트 임베딩 생성', exact: true}).click();
  await fs.mkdir('.impeccable/review', {recursive: true});
  for (const width of [1440, 1024, 390]) {
    await page.setViewportSize({width, height: width === 390 ? 844 : 1000});
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `overflow at ${width}`);
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.waitForTimeout(300);
    await page.screenshot({path: `.impeccable/review/tasks-${width}.png`, fullPage: false});
    if (width === 390) {
      await page.locator('.detail').scrollIntoViewIfNeeded();
      assert.ok((await page.locator('.detail').innerText()).includes('요청자 other-us'));
      await page.waitForTimeout(300);
      await page.screenshot({path: '.impeccable/review/tasks-mobile-detail.png', fullPage: false});
    }
  }
  testingPagination = true;
  await page.reload();
  await page.getByRole('button', {name: '다음 작업', exact: true}).waitFor();
  await page.getByRole('button', {name: '다음 작업', exact: true}).click();
  await page.getByRole('heading', {name: '더 표시할 작업이 없습니다.', exact: true}).waitFor();
  assert.equal(await page.getByRole('button', {name: '첫 작업 만들기', exact: true}).count(), 0);
  await page.screenshot({path: '.impeccable/review/tasks-empty-page.png', fullPage: false});
  await page.getByRole('button', {name: '이전 목록으로', exact: true}).click();
  await page.waitForFunction(() => document.querySelectorAll('.jobs-table tbody > tr').length === 100);
  testingPagination = false;
  approved = false;
  await page.reload();
  await page.getByRole('heading', {name: '관리자 승인을 기다리고 있습니다.'}).waitFor();
  assert.equal(await page.locator('.jobs-table').count(), 0);
  assert.deepEqual(errors, []);
  await browser.close();
  console.log('Unified work UI passed: all job sources, detail, filter, cancellation routing, AI creation, service availability, usage, approval and responsive layouts. Synthetic fixtures.');
})().catch(error => {console.error(error); process.exit(1);});
