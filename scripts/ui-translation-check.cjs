// Synthetic UI records; real n8n translation is checked separately.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'chrome',args:['--disable-gpu']});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  const now=new Date().toISOString(),future=new Date(Date.now()+86400000).toISOString();
  const service={kind:'text.translate',service:'translation',label:'문장 번역 · n8n',interface:'translation',input_type:'text',available:true};
  const data={id:'translation-fixture',request_id:'synthetic-fixture',task_type:'text.translate',project:'fixture',environment:'test',status:'succeeded',created_at:now,finished_at:now,expires_at:future,result:{type:'text_translate',translated_text:'Synthetic translation, not a real user document.\n'+ 'This is a long sample sentence. '.repeat(80),source_language:'ko',target_language:'en',provider:'fixture',model:'fixture-model'}};
  const complete={source:'ai',id:data.id,kind:service.kind,service:service.service,title:'가상 문장 번역',label:service.label,status:'succeeded',executor:'n8n',origin:'web',owner_id:'fixture',created_at:now,data};
  const tasks=[complete];let payload,failed=true,approved=true,cancelPath;
  await page.route('**/api/**',async route=>{
   const req=route.request(),url=new URL(req.url()),path=url.pathname;let body=[],status=200;
   if(path==='/api/me')body={id:'fixture',role:'admin',status:approved?'approved':'pending',csrf:'fixture'};
   else if(path==='/api/services')body=[service];
   else if(path==='/api/tasks')body=tasks.filter(t=>!url.searchParams.has('service')||url.searchParams.get('service')===t.service);
   else if(path==='/api/compute')body={concurrency:1,requests:[]};
   else if(path==='/api/translations'){
    const received=req.postDataJSON();if(payload)assert.deepEqual(received,payload);payload=received;
    if(failed){failed=false;status=503;body={detail:'translation_not_configured'};}
    else{status=202;body={...data,id:'translation-new',status:'pending',result:null};tasks.unshift({...complete,id:body.id,title:'새 번역 대기',status:'queued',data:body});}
   }else if(path.endsWith('/cancel')){cancelPath=path;tasks[0].status='cancelled';tasks[0].data.status='cancelled';body={cancelled:true};}
   await route.fulfill({json:body,status});
  });
  async function capture(name){
   await fs.mkdir('.impeccable/review',{recursive:true});
   for(const [device,width]of[['desktop',1440],['mobile',390]]){
    await page.setViewportSize({width,height:1000});await page.evaluate(()=>scrollTo(0,0));await page.evaluate(()=>document.fonts.ready);await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    await page.screenshot({path:`.impeccable/review/translation-${name}-${device}.png`,fullPage:true,animations:'disabled'});
   }
  }
  await page.goto('http://127.0.0.1:5173/');
  await page.getByRole('button',{name:'서비스',exact:true}).click();
  await page.getByRole('heading',{name:service.label,exact:true}).waitFor();
  await page.getByRole('button',{name:'작업 만들기',exact:true}).click();
  await page.getByLabel('원문 언어',{exact:true}).selectOption('ko');
  await page.getByLabel('번역 언어',{exact:true}).selectOption('en');
  const input=page.getByLabel('번역할 문장',{exact:true});await input.fill('가상 번역 입력입니다.');assert.equal(await input.getAttribute('maxlength'),'4000');
  await capture('form');
  await page.getByRole('button',{name:'작업 시작',exact:true}).click();await page.getByRole('alert').filter({hasText:'번역 연결이 준비되지 않았습니다.'}).waitFor();
  await page.getByRole('button',{name:'작업 시작',exact:true}).click();
  assert.equal(payload.source_language,'ko');assert.equal(payload.target_language,'en');assert.equal(payload.text,'가상 번역 입력입니다.');
  await page.getByRole('button',{name:'새 번역 대기',exact:true}).click();await page.getByRole('button',{name:'작업 취소',exact:true}).click();assert.equal(cancelPath,'/api/ai/jobs/translation-new/cancel');
  await page.getByRole('button',{name:complete.title,exact:true}).click();
  const region=page.getByLabel('번역문',{exact:true});await region.focus();await page.keyboard.press('ArrowDown');await page.waitForTimeout(180);assert.ok(await region.evaluate(el=>el.scrollTop>0));assert.equal(await region.evaluate(el=>getComputedStyle(el).outlineWidth),'3px');
  assert.match(await page.getByRole('link',{name:'번역문 TXT 다운로드',exact:true}).getAttribute('href'),/translation-fixture\/translation.txt$/);
  await capture('result');
  data.expires_at=new Date(Date.now()-1000).toISOString();await page.reload();await page.getByRole('button',{name:complete.title,exact:true}).click();await page.getByText('결과 보관 기간이 만료되었습니다.',{exact:true}).waitFor();assert.equal(await page.getByRole('link',{name:'번역문 TXT 다운로드',exact:true}).count(),0);
  approved=false;await page.reload();await page.getByText('관리자 승인을 기다리고 있습니다.',{exact:true}).waitFor();assert.deepEqual(errors,[]);
  console.log('Translation catalog/form/options, admission retry, common work/cancel, result/TXT/keyboard/expiry/auth and responsive UI: PASS');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
