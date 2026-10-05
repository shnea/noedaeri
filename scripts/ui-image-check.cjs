const { chromium } = require('playwright');
const { execFileSync } = require('node:child_process');
const fs = require('node:fs/promises');
const assert = require('node:assert/strict');
(async () => {
  await fs.mkdir('tmp',{recursive:true});
  const root = await fs.mkdtemp('tmp/image-ui-');
  const browser = await chromium.launch({headless:true,channel:'chrome',args:['--disable-gpu']});
  try {
    execFileSync('.venv/bin/python',['-c',`from pathlib import Path
from PIL import Image, ImageDraw
from noedaeri.images import convert
import sys,json
p=Path(sys.argv[1]); im=Image.new('RGBA',(1200,800),(220,240,225,255)); d=ImageDraw.Draw(im);d.rectangle((100,100,500,700),fill=(30,110,90,200));d.ellipse((600,150,1100,650),fill=(230,170,70,255));im.save(p/'input.png');result=convert(p/'input.png',p/'out','png');(p/'manifest.json').write_text(json.dumps(result))`,root]);
    const manifest = JSON.parse(await fs.readFile(root+'/manifest.json','utf8'));
    const page = await browser.newPage({viewport:{width:1440,height:1100}});
    const now=new Date().toISOString();let payload;
    const job={id:'00000000-0000-4000-8000-000000000001',title:'이미지 통합 처리 검증 예시',kind:'image.package',service:'image',status:'succeeded',stage:'finished',owner_id:'fixture',worker_id:'fixture',created_at:now,updated_at:now,finished_at:now,expires_at:new Date(Date.now()+86400000).toISOString(),cancel_requested:false,error_code:null,cleanup_state:'done',result_state:'available',result:manifest};
    await page.route('**/api/**',async route=>{
      const path=new URL(route.request().url()).pathname;
      if(path.endsWith('/preview.webp'))return route.fulfill({body:await fs.readFile(root+'/out/preview.webp'),contentType:'image/webp'});
      if(path==='/api/jobs'&&route.request().method()==='POST'){payload=route.request().postDataJSON();return route.fulfill({json:{...job,status:'uploading'}});}
      const data=path==='/api/me'?{id:'fixture',role:'admin',status:'approved',csrf:'fixture'}:path==='/api/jobs'?[job]:path==='/api/services'?[{kind:'image.package',service:'image',label:'이미지 통합 처리',input_type:'upload'}]:[];
      return route.fulfill({json:data});
    });
    await page.goto('http://127.0.0.1:5173/');
    await page.getByRole('button',{name:job.title,exact:true}).click();
    await page.getByAltText('생성된 이미지 미리보기').waitFor();
    await page.waitForFunction(()=>document.querySelector('.result-preview img')?.naturalWidth>0);
    assert.equal(await page.getByRole('link',{name:'WebP 미리보기',exact:true}).count(),1);
    await fs.mkdir('.impeccable/review',{recursive:true});
    await page.evaluate(()=>scrollTo(0,0));await page.waitForTimeout(300);
    await page.screenshot({path:'.impeccable/review/image-desktop.png'});
    await page.setViewportSize({width:390,height:844});
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    const height=await page.evaluate(()=>document.documentElement.scrollHeight);
    await page.setViewportSize({width:390,height});await page.evaluate(()=>scrollTo(0,0));await page.waitForTimeout(300);
    await page.screenshot({path:'.impeccable/review/image-mobile.png'});
    await page.getByRole('button',{name:'새 작업',exact:true}).click();
    await page.getByLabel('작업명',{exact:true}).fill('이미지 요청');
    await page.getByLabel('입력 이미지',{exact:true}).setInputFiles(root+'/input.png');
    await page.getByRole('button',{name:'작업 시작',exact:true}).click();
    await page.getByRole('heading',{name:'새 작업',exact:true}).waitFor({state:'hidden'});
    assert.deepEqual(payload.input,{type:'upload',extension:'png'});
    assert.deepEqual(payload.options,{});
    console.log('Image creation payload, preview, derivative links, desktop/mobile: PASS');
  }finally{await browser.close();await fs.rm(root,{recursive:true,force:true});}
})().catch(error=>{console.error(error);process.exit(1)});
