const {chromium}=require('playwright');
const fs=require('node:fs/promises');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'chrome',args:['--disable-gpu']});
 try{
 const page=await browser.newPage({viewport:{width:1440,height:1100}});
 const now=new Date().toISOString();let sent=0;
 const job={id:'00000000-0000-4000-8000-000000000001',title:'플랫폼 영상 변환',kind:'video.package',service:'ffmpeg',origin:'platform',status:'failed',stage:'encoding_720p',owner_id:'service',worker_id:'fixture',created_at:now,updated_at:now,finished_at:now,expires_at:null,cancel_requested:false,error_code:'processing_timeout',cleanup_state:'done',result_state:'none',result:null,delivery:{state:'failed',attempts:8,last_http_status:503,next_attempt_at:now}};
 await page.route('**/api/**',async route=>{
 const path=new URL(route.request().url()).pathname;
 if(path.endsWith('/webhook-retry')){sent++;job.delivery.state='pending';job.delivery.attempts=0;return route.fulfill({json:{accepted:true}});}
 const data=path==='/api/me'?{id:'fixture',role:'admin',status:'approved',csrf:'fixture'}:path==='/api/jobs'?[job]:[];
 return route.fulfill({json:data});
 });
 await page.goto('http://127.0.0.1:5173/');
 await page.getByRole('button',{name:job.title,exact:true}).click();
 await page.getByText('완료 알림 전송 실패',{exact:true}).waitFor();
 assert.equal(await page.getByRole('button',{name:'입력 다시 올려 재시도'}).count(),0);
 await fs.mkdir('.impeccable/review',{recursive:true});
 await page.evaluate(()=>scrollTo(0,0));await page.waitForTimeout(300);
 await page.screenshot({path:'.impeccable/review/platform-desktop.png'});
 await page.setViewportSize({width:390,height:844});
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 const height=await page.evaluate(()=>document.documentElement.scrollHeight);
 await page.setViewportSize({width:390,height});await page.evaluate(()=>scrollTo(0,0));await page.waitForTimeout(300);
 await page.screenshot({path:'.impeccable/review/platform-mobile.png'});
 await page.getByRole('button',{name:'완료 알림 다시 전송'}).click();
 await page.getByText('완료 알림 전송 대기',{exact:true}).waitFor();
 assert.equal(sent,1);
 console.log('Platform origin, webhook state, redelivery without job rerun, desktop/mobile: PASS');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1)});
