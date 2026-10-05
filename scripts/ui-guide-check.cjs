const { chromium } = require('playwright');
const fs = require('node:fs/promises');
const assert = require('node:assert/strict');
(async () => {
  const browser = await chromium.launch({headless:true,channel:'chrome',args:['--disable-gpu']});
  try {
    const context = await browser.newContext({viewport:{width:1440,height:1000},permissions:['clipboard-read','clipboard-write']});
    const page = await context.newPage();
    const document = await fs.readFile('docs/SERVICE_INTEGRATION.md','utf8');
    await page.route('**/api/**',route => {
      const path = new URL(route.request().url()).pathname;
      if (path === '/api/integrations/guide') return route.fulfill({body:document,contentType:'text/markdown'});
      return route.fulfill({json:path==='/api/me'?{id:'fixture',role:'admin',status:'approved',csrf:'fixture'}:[]});
    });
    await page.goto('http://127.0.0.1:5173');
    await page.getByRole('button',{name:'연동 지침',exact:true}).click();
    await page.getByRole('heading',{name:'뇌대리 서비스 연동 지침'}).waitFor();
    assert.ok(await page.locator('.guide-document table').count() >= 4);
    await page.getByRole('button',{name:'지침 전체 복사'}).click();
    await page.getByRole('button',{name:'복사 완료'}).waitFor();
    assert.equal(await page.evaluate(() => navigator.clipboard.readText()),document);
    await fs.mkdir('.impeccable/review',{recursive:true});
    await page.evaluate(() => window.scrollTo(0,0));
    await page.waitForTimeout(300);
    await page.screenshot({path:'.impeccable/review/guide-desktop.png'});
    await page.setViewportSize({width:390,height:1000});
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.evaluate(() => window.scrollTo(0,0));
    await page.waitForTimeout(300);
    await page.screenshot({path:'.impeccable/review/guide-mobile.png'});
    console.log('Guide menu, Markdown tables, exact copy and mobile overflow: PASS');
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exit(1)});
