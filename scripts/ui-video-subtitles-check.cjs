// Synthetic identity and metadata with real rendered fixture HLS; no production auth bypass.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
const {mediaTasks}=require('./ui-fixtures.cjs');
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'chrome',args:['--disable-gpu']});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
  page.setDefaultTimeout(10000);
  page.on('pageerror',error=>errors.push(error.message));
  const root='tmp/video-caption-review',now=new Date().toISOString(),future=new Date(Date.now()+86400000).toISOString();
  let approved=true,payload,uploaded=false,failAdmission=true,canBurn=true;
  const service={kind:'video.package',service:'ffmpeg',label:'영상 통합 처리 · 썸네일 + 해상도별 스트리밍',input_type:'upload',available:true,
   subtitle_support:{sidecar:true,burned:true,max_duration_seconds:3600,transcription_timeout_seconds:900,total_timeout_seconds:1800,timing:'vad_proportional'}};
  const jobs=[];
  for(const mode of ['burned','sidecar'])jobs.push({id:mode+'-caption-fixture',title:mode==='burned'?'자막을 입힌 영상 · 검수 예시':'선택 자막 영상 · 검수 예시',kind:service.kind,service:'ffmpeg',origin:'web',
   options:{seconds:0,subtitles:{mode,language:'ko',use_itn:true}},owner_id:'fixture-user',worker_id:'fixture-worker',created_at:now,updated_at:now,finished_at:now,expires_at:future,
   cancel_requested:false,error_code:null,cleanup_state:'done',result_state:'available',status:'succeeded',stage:'finished',result:JSON.parse(await fs.readFile(path.join(root,mode,'manifest.json'),'utf8'))});
  await page.route('**/api/**',async route=>{
   const req=route.request(),url=new URL(req.url()),pathname=url.pathname;
   if(pathname.includes('/files/')){
    const mode=pathname.includes('sidecar-')?'sidecar':'burned',name=path.basename(pathname);
    if(!jobs.find(job=>job.id===mode+'-caption-fixture').result.files.includes(name))return route.fulfill({status:404});
    return route.fulfill({body:await fs.readFile(path.join(root,mode,name)),contentType:name.endsWith('.m3u8')?'application/vnd.apple.mpegurl':name.endsWith('.ts')?'video/mp2t':name.endsWith('.vtt')?'text/vtt':'image/jpeg'});
   }
   let body=[];
   if(pathname==='/api/me')body={id:'fixture-user',role:'admin',status:approved?'approved':'pending',csrf:'fixture'};
   else if(pathname==='/api/compute')body={concurrency:1,requests:[]};
   else if(pathname==='/api/services')body=[{...service,subtitle_support:{...service.subtitle_support,burned:canBurn}}];
   else if(pathname==='/api/tasks')body=mediaTasks(jobs,req.url());
   else if(pathname==='/api/jobs'&&req.method()==='POST'){
    payload=req.postDataJSON();
    if(failAdmission){failAdmission=false;return route.fulfill({status:503,json:{detail:'subtitle_renderer_unavailable'}});}
    body={...jobs[0],id:'new-caption-job',title:payload.title,options:payload.options,status:'uploading',stage:'awaiting_input',result:null,result_state:'none',finished_at:null,expires_at:null};
    jobs.unshift({...body,status:'queued',stage:'waiting_compute'});
   }else if(pathname.endsWith('/input')){uploaded=true;body={status:'queued'};}
   else if(pathname.endsWith('/cancel')){jobs[0].status='cancelled';body={status:'cancellation_requested'};}
   await route.fulfill({json:body});
  });
  async function capture(name){
   await fs.mkdir('.impeccable/review',{recursive:true});
   for(const [device,width]of[['desktop',1440],['mobile',390]]){
    await page.setViewportSize({width,height:1000});await page.evaluate(()=>scrollTo(0,0));await page.evaluate(()=>document.fonts.ready);
    await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    await page.screenshot({path:`.impeccable/review/video-captions-${name}-${device}.png`,fullPage:true,animations:'disabled'});
   }
  }
  await page.goto('http://127.0.0.1:5173/');
  await page.getByRole('button',{name:'서비스',exact:true}).click();
  await page.getByRole('heading',{name:service.label,exact:true}).waitFor();
  await page.getByRole('button',{name:'작업 만들기',exact:true}).click();
  assert.equal(await page.getByLabel('영상 자막',{exact:true}).inputValue(),'none');
  await page.getByLabel('작업명',{exact:true}).fill('새 자막 통합 작업 · 검수 예시');
  await page.getByLabel('영상 자막',{exact:true}).selectOption('burned');
  await page.getByLabel('인식 언어',{exact:true}).selectOption('ko');
  await page.getByLabel('숫자·문장 표기',{exact:true}).selectOption('off');
  await page.getByLabel('입력 영상',{exact:true}).setInputFiles(path.join(root,'source.mp4'));
  await page.getByLabel('영상 자막',{exact:true}).focus();
  assert.equal(await page.getByLabel('영상 자막',{exact:true}).evaluate(el=>getComputedStyle(el).outlineWidth),'3px');
  await capture('form');
  await page.getByRole('button',{name:'작업 시작',exact:true}).click();
  await page.getByRole('alert').filter({hasText:'libass 지원 FFmpeg 설정'}).waitFor();
  await page.getByRole('button',{name:'같은 입력으로 다시 시도',exact:true}).click();
  await page.getByRole('button',{name:'새 자막 통합 작업 · 검수 예시',exact:true}).waitFor();
  assert.ok(uploaded);assert.equal(payload.kind,'video.package');assert.deepEqual(payload.options,{seconds:0,subtitles:{mode:'burned',language:'ko',use_itn:false}});
  await page.getByRole('button',{name:'새 자막 통합 작업 · 검수 예시',exact:true}).click();
  await page.getByRole('button',{name:'작업 취소',exact:true}).click();
  for(const mode of ['burned','sidecar']){
   const job=jobs.find(job=>job.id===mode+'-caption-fixture');
   await page.getByRole('button',{name:job.title,exact:true}).click();
   await page.waitForFunction(()=>document.querySelector('video')?.readyState>=2);
   await page.locator('video').evaluate(async element=>{element.muted=true;await element.play()});
   await page.waitForFunction(()=>document.querySelector('video')?.currentTime>0.5);
   await page.getByLabel('재생 화질').selectOption('1');
   await page.waitForFunction(()=>document.querySelector('video')?.videoHeight===720);
   await page.locator('video').evaluate(video=>{video.currentTime=1;});
   await page.waitForFunction(()=>Math.abs(document.querySelector('video')?.currentTime-1)<0.1);
   if(mode==='burned'){
    assert.equal(await page.locator('video track').count(),0);
    await page.getByText(/모든 화질에 적용되며 재생 중 끌 수 없습니다/).waitFor();
   }else{
    await page.waitForFunction(()=>document.querySelector('video')?.textTracks[0]?.cues?.length===1);
    await page.locator('video').evaluate(video=>{video.textTracks[0].mode='disabled'});
    assert.equal(await page.locator('video').evaluate(video=>video.textTracks[0].mode),'disabled');
    await page.locator('video').evaluate(video=>{video.textTracks[0].mode='showing'});
   }
   assert.match(await page.getByRole('link',{name:'SRT 자막',exact:true}).getAttribute('href'),/files\/subtitles.srt$/);
   await page.mouse.move(0,0);await page.waitForTimeout(2500);
   await capture(mode);
  }
  const sidecar=jobs.find(job=>job.id==='sidecar-caption-fixture');sidecar.result_state='expired';sidecar.expires_at=new Date(Date.now()-1000).toISOString();
  await page.reload();await page.getByRole('button',{name:sidecar.title,exact:true}).click();await page.getByText('보관 기간이 만료되었습니다.',{exact:true}).waitFor();assert.equal(await page.locator('video').count(),0);
  canBurn=false;await page.reload();await page.getByRole('button',{name:'새 작업',exact:true}).click();
  await page.getByText('자막 입히기를 사용할 수 없습니다.',{exact:false}).waitFor();
  assert.equal(await page.getByLabel('영상 자막',{exact:true}).locator('option[value="burned"]').evaluate(option=>option.disabled),true);
  approved=false;await page.reload();await page.getByText('관리자 승인을 기다리고 있습니다.',{exact:true}).waitFor();
  assert.deepEqual(errors,[]);
  console.log('Video subtitle form/admission/retry/upload, unified jobs/cancel, real HLS 720p, sidecar track toggle, burned captions, expiry/auth, keyboard/responsive: PASS');
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
