const { mediaTasks } = require('./ui-fixtures.cjs');
const {chromium}=require('playwright');
const fs=require('node:fs/promises');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'chrome',args:['--disable-gpu']});
 try{
 const page=await browser.newPage({viewport:{width:1440,height:1100}});
 const now=new Date().toISOString();let payload;
 const job={id:'00000000-0000-4000-8000-000000000001',title:'변환 중단 후 재시도',kind:'video.package',service:'ffmpeg',status:'failed',stage:'encoding_720p',owner_id:'fixture',worker_id:'fixture',created_at:now,updated_at:now,finished_at:now,expires_at:null,cancel_requested:false,error_code:'lease_lost',cleanup_state:'done',result_state:'none',result:null,options:{seconds:2},output_reserved:0};
 await page.route('**/api/**',async route=>{
 const path=new URL(route.request().url()).pathname;
 if(path==='/api/jobs'&&route.request().method()==='POST'){payload=route.request().postDataJSON();return route.fulfill({json:{...job,id:'00000000-0000-4000-8000-000000000002',status:'uploading'}});}
 const data=path==='/api/me'?{id:'fixture',role:'admin',status:'approved',csrf:'fixture'}:path==='/api/tasks'?mediaTasks([job],route.request().url()):path==='/api/services'?[{kind:'video.package',service:'ffmpeg',label:'영상 통합 처리',input_type:'upload'}]:[];
 return route.fulfill({json:data});
 });
 await page.goto('http://127.0.0.1:5173/');
 await page.getByRole('button',{name:job.title,exact:true}).click();
 await page.getByRole('button',{name:'입력 다시 올려 재시도'}).focus();
 await page.keyboard.press('Enter');
 await page.waitForFunction(()=>document.activeElement?.tagName==='INPUT');
 assert.ok(await page.getByLabel('작업명',{exact:true}).evaluate(el=>el===document.activeElement));
 assert.equal(await page.getByLabel('작업명',{exact:true}).inputValue(),job.title);
 assert.ok(await page.locator('.form-fields select').isDisabled());
 await fs.mkdir('.impeccable/review',{recursive:true});
 await page.evaluate(()=>scrollTo(0,0));await page.waitForTimeout(300);
 await page.screenshot({path:'.impeccable/review/recovery-desktop.png'});
 await page.setViewportSize({width:390,height:844});
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 const height=await page.evaluate(()=>document.documentElement.scrollHeight);
 await page.setViewportSize({width:390,height});await page.evaluate(()=>scrollTo(0,0));await page.waitForTimeout(300);
 await page.screenshot({path:'.impeccable/review/recovery-mobile.png'});
 await page.getByLabel('입력 영상',{exact:true}).setInputFiles({name:'fixture.mp4',mimeType:'video/mp4',buffer:Buffer.from('mock upload')});
 await page.getByRole('button',{name:'작업 시작',exact:true}).click();
 await page.getByRole('heading',{name:'새 작업',exact:true}).waitFor({state:'hidden'});
 assert.equal(payload.retry_of,job.id);assert.equal(payload.options.seconds,2);
 console.log('Retry selection, immutable kind, reupload, new request, desktop/mobile: PASS');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1)});
