// Synthetic AI notification states; real receiver verification belongs to platform rollout.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'chrome',args:['--disable-gpu']});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  let admin=true,retryCount=0;
  const now=new Date().toISOString();
  const data={id:'ai-fixture',request_id:'synthetic-fixture',task_type:'text.translate',project:'fixture',environment:'test',status:'succeeded',created_at:now,finished_at:now,expires_at:new Date(Date.now()+86400000).toISOString(),notify:true,received_at:null,delivery:{state:'failed',configured:true,attempts:8,last_http_status:503,next_attempt_at:now},result:{type:'text_translate',translated_text:'A synthetic translation used only for interface review.',source_language:'ko',target_language:'en',provider:'fixture',model:'fixture-model'}};
  const task={source:'ai',id:data.id,kind:'text.translate',service:'translation',title:'가상 플랫폼 번역',label:'문장 번역 · n8n',status:'succeeded',executor:'n8n',origin:'platform',owner_id:'platform-fixture',created_at:now,data};
  await page.route('**/api/**',async route=>{
   const path=new URL(route.request().url()).pathname;let body=[];
   if(path==='/api/me')body={id:'fixture',role:admin?'admin':'user',status:'approved',csrf:'fixture'};
   else if(path==='/api/tasks')body=[task];
   else if(path==='/api/compute')body={concurrency:1,requests:[]};
   else if(path==='/api/admin/ai/jobs/ai-fixture/webhook-retry'){
    assert.equal(route.request().method(),'POST');retryCount++;data.delivery.state='pending';data.delivery.attempts=0;body={accepted:true};
   }
   await route.fulfill({json:body});
  });
  async function open(){await page.reload();await page.getByRole('button',{name:task.title,exact:true}).click();}
  async function capture(name){
   await fs.mkdir('.impeccable/review',{recursive:true});
   for(const[device,width]of[['desktop',1440],['mobile',390]]){
    await page.setViewportSize({width,height:1000});await page.evaluate(()=>scrollTo(0,0));await page.evaluate(()=>document.fonts.ready);await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    if(name==='failed'){const retry=page.getByRole('button',{name:'완료 알림 다시 보내기',exact:true});await retry.focus();assert.equal(await retry.evaluate(e=>getComputedStyle(e).outlineWidth),'3px');}
    await page.screenshot({path:`.impeccable/review/ai-delivery-${name}-${device}.png`,fullPage:true,animations:'disabled'});
   }
  }
  await page.goto('http://127.0.0.1:5173/');await page.getByRole('button',{name:task.title,exact:true}).click();
  await page.getByText('완료 알림 전송 실패',{exact:true}).waitFor();
  const retry=page.getByRole('button',{name:'완료 알림 다시 보내기',exact:true});await retry.focus();assert.equal(await retry.evaluate(e=>getComputedStyle(e).outlineWidth),'3px');await capture('failed');
  await retry.click();assert.equal(retryCount,1);await page.getByText('완료 알림 전송 대기',{exact:true}).waitFor();
  data.delivery.configured=false;await open();await page.getByText('AI 수신 주소 설정 필요',{exact:true}).waitFor();
  data.delivery.configured=true;data.delivery.state='delivered';await open();await page.getByText('완료 알림 수신됨 · 결과 수령 확인 대기',{exact:true}).waitFor();
  task.status='failed';data.status='failed';await open();await page.getByText('완료 알림 수신됨',{exact:true}).waitFor();task.status='succeeded';data.status='succeeded';
  data.received_at=now;data.expires_at=now;data.result=null;data.delivery.state='acknowledged';await open();await page.getByText('결과 수령 확인됨',{exact:true}).waitFor();assert.equal(await page.getByRole('link',{name:'번역문 TXT 다운로드',exact:true}).count(),0);await capture('received');
  data.received_at=null;data.delivery.state='failed';admin=false;await open();assert.equal(await page.getByRole('button',{name:'완료 알림 다시 보내기',exact:true}).count(),0);
  assert.deepEqual(errors,[]);console.log('AI delivery failed/retry/pending/configuration/delivered/receipt/role/expiry and responsive UI: PASS');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
