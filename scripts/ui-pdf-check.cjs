// Synthetic PDF records verify UI behavior; native PDFKit/OCR is tested separately.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {mediaTasks}=require('./ui-fixtures.cjs');
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'chrome',args:['--disable-gpu']});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  const now=new Date().toISOString(), future=new Date(Date.now()+86400000).toISOString();
  const service={kind:'pdf.extract',service:'pdf',label:'PDF 텍스트 추출 · 스캔 페이지 OCR',interface:'media',input_type:'upload',available:true,pdf_limits:{max_input_bytes:200000000,max_pages:100,timeout_seconds:900,processing_dimension:4096}};
  const complete={id:'pdf-complete-fixture',kind:service.kind,service:'pdf',title:'가상 PDF 텍스트 추출',status:'succeeded',stage:'finished',owner_id:'fixture',worker_id:'native-worker',created_at:now,updated_at:now,finished_at:now,expires_at:future,cancel_requested:false,error_code:null,cleanup_state:'done',result_state:'available',result:{type:'pdf_extract',page_count:3,files:['document.json','document.txt','document.zip']}};
  const jobs=[complete];let payload,uploaded=false,failed=true,approved=true;
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/api/**',async route=>{
   const req=route.request(),path=new URL(req.url()).pathname;let body=[],status=200;
   if(path==='/api/me')body={id:'fixture',role:'admin',status:approved?'approved':'pending',csrf:'fixture'};
   else if(path==='/api/services')body=[service];
   else if(path==='/api/tasks')body=mediaTasks(jobs,req.url());
   else if(path==='/api/compute')body={concurrency:1,requests:[]};
   else if(path==='/api/jobs'&&req.method()==='POST'){payload=req.postDataJSON();body={...complete,...payload,id:'new-pdf-fixture',status:'uploading',result:null,result_state:'none',finished_at:null,expires_at:null};jobs.unshift(body);status=201;}
   else if(path.endsWith('/input')){uploaded=true;jobs[0].status='running';jobs[0].stage='pdf_pages_1_of_3';body={status:'running'};}
   else if(path.endsWith('/cancel')){jobs[0].status='cancelled';body={accepted:true};}
   else if(path.endsWith('/files/document.json')){
    if(failed){failed=false;status=503;body={detail:'result_missing'};}
    else body={page_count:3,pages:[{page:1,method:'text',text:'가상 PDF 테스트 결과입니다. 실제 문서와 구분합니다.\n'+ 'LongToken'.repeat(50)},{page:2,method:'ocr',text:'가상 스캔 OCR 결과입니다.'},{page:3,method:'text',text:''}]};
   }
   await route.fulfill({json:body,status});
  });
  async function capture(name){
   await fs.mkdir('.impeccable/review',{recursive:true});
   for(const [device,width] of [['desktop',1440],['mobile',390]]){
    await page.setViewportSize({width,height:1000});await page.evaluate(()=>scrollTo(0,0));await page.evaluate(()=>document.fonts.ready);await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    await page.screenshot({path:'.impeccable/review/pdf-'+name+'-'+device+'.png',fullPage:true,animations:'disabled'});
   }
  }
  await page.goto('http://127.0.0.1:5173/');
  await page.getByRole('button',{name:'서비스',exact:true}).click();
  await page.getByRole('heading',{name:service.label,exact:true}).waitFor();
  await page.getByRole('button',{name:'작업 만들기',exact:true}).click();
  await page.getByLabel('작업명',{exact:true}).fill('PDF 문서 검수');
  await page.getByLabel('입력 PDF',{exact:true}).setInputFiles({name:'fixture.pdf',mimeType:'application/pdf',buffer:Buffer.from('%PDF-synthetic UI input')});
  await page.getByLabel('인식 언어',{exact:true}).selectOption('ko');
  await page.getByLabel('추출 방식',{exact:true}).selectOption('auto');
  await page.getByText(/PDF 최대 200MB/).waitFor();await capture('form');
  await page.getByRole('button',{name:'작업 시작',exact:true}).click();
  await page.getByRole('button',{name:'PDF 문서 검수',exact:true}).click();
  await page.getByText('PDF 1 / 3페이지 처리 완료',{exact:true}).waitFor();
  assert.equal(uploaded,true);assert.deepEqual(payload.input,{type:'upload'});assert.deepEqual(payload.options,{mode:'auto',language:'ko',language_correction:true});
  await page.getByRole('button',{name:'작업 취소',exact:true}).click();
  await page.getByRole('button',{name:complete.title,exact:true}).click();
  await page.getByRole('button',{name:'결과 다시 불러오기',exact:true}).click();
  const textRegion=page.getByLabel('PDF 페이지 텍스트',{exact:true});await textRegion.waitFor();await textRegion.focus();await page.keyboard.press('ArrowDown');await page.waitForTimeout(180);assert.equal(await textRegion.getAttribute('tabindex'),'0');assert.ok(await textRegion.evaluate(el=>el.scrollTop>0));assert.equal(await textRegion.evaluate(el=>getComputedStyle(el).outlineWidth),'3px');await capture('result');
  await page.getByRole('button',{name:'다음 페이지',exact:true}).click();await page.getByText('2 / 3페이지 · 스캔 OCR',{exact:true}).waitFor();
  await page.getByRole('button',{name:'다음 페이지',exact:true}).click();await page.getByText('이 페이지에서 추출한 텍스트가 없습니다.',{exact:true}).waitFor();
  assert.equal(await page.getByRole('button',{name:'다음 페이지',exact:true}).isDisabled(),true);
  assert.match(await page.getByRole('link',{name:'페이지 JSON 다운로드',exact:true}).getAttribute('href'),/document.json$/);
  complete.expires_at=new Date(Date.now()-1000).toISOString();complete.result_state='expired';await page.reload();await page.getByRole('button',{name:complete.title,exact:true}).click();await page.getByText('보관 기간이 만료되었습니다.',{exact:true}).waitFor();
  assert.equal(await page.getByRole('link',{name:'전체 텍스트 다운로드',exact:true}).count(),0);
  approved=false;await page.reload();await page.getByText('관리자 승인을 기다리고 있습니다.',{exact:true}).waitFor();assert.deepEqual(errors,[]);
  console.log('PDF service/form, upload/options, page progress/cancel, retry/page/empty/download/expiry/auth and responsive UI: PASS');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
