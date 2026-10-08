const { mediaTasks } = require('./ui-fixtures.cjs');
// Synthetic media and identity only; never operates a production session.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
(async () => {
  const result = JSON.parse(await fs.readFile('tmp/ui-video/manifest.json', 'utf8'));
  const browser = await chromium.launch({headless:true,channel:'chrome',args:['--disable-gpu']});
  try {
    const page = await browser.newPage({viewport:{width:1440,height:1100}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const now = new Date().toISOString();
    const job = {id:'00000000-0000-4000-8000-000000000001',title:'영상 통합 처리 검증 예시',kind:'video.package',service:'ffmpeg',status:'succeeded',stage:'finished',owner_id:'fixture-user',worker_id:'fixture-worker',created_at:now,updated_at:now,finished_at:now,expires_at:new Date(Date.now()+86400000).toISOString(),cancel_requested:false,error_code:null,cleanup_state:'done',result_state:'available',result};
    await page.route('**/api/**', async route => {
      const url = new URL(route.request().url());
      if (url.pathname.includes('/files/')) {
        const name = path.basename(url.pathname);
        if (!result.files.includes(name)) return route.fulfill({status:404});
        return route.fulfill({body:await fs.readFile(path.join('tmp/ui-video',name)),contentType:name.endsWith('.m3u8')?'application/vnd.apple.mpegurl':name.endsWith('.ts')?'video/mp2t':'image/jpeg'});
      }
      const data = url.pathname==='/api/me'?{id:'fixture-user',role:'user',status:'approved',csrf:'fixture'}:url.pathname==='/api/tasks'?mediaTasks([job],route.request().url()):[];
      return route.fulfill({json:data});
    });
    await page.goto('http://127.0.0.1:5173/');
    await page.getByRole('button',{name:job.title,exact:true}).click();
    await page.waitForFunction(() => document.querySelector('video')?.readyState >= 2);
    await page.locator('video').evaluate(video => video.play());
    await page.waitForFunction(() => document.querySelector('video').currentTime > 0.5);
    await page.getByLabel('재생 화질').selectOption('1');
    await page.waitForFunction(() => document.querySelector('video').videoHeight === 720);
    await page.getByLabel('재생 화질').selectOption('-1');
    await page.locator('video').evaluate(video => video.pause());
    await fs.mkdir('.impeccable/review',{recursive:true});
    await page.evaluate(() => window.scrollTo(0,0));
    await page.waitForTimeout(300);
    await page.screenshot({path:'.impeccable/review/video-desktop.png'});
    await page.setViewportSize({width:390,height:1200});
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    const height = await page.evaluate(() => document.documentElement.scrollHeight);
    await page.setViewportSize({width:390,height});
    await page.evaluate(() => window.scrollTo(0,0));
    await page.waitForTimeout(300);
    await page.screenshot({path:'.impeccable/review/video-mobile.png'});
    assert.deepEqual(errors,[]);
    console.log('HLS playback, manual 720p, automatic selection, desktop/mobile: PASS');
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exit(1)});
