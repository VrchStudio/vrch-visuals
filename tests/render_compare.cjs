'use strict';
const fs=require('node:fs'),path=require('node:path'),http=require('node:http'),cp=require('node:child_process'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'..'),config=require('../fixtures/render-config.json');
const read=p=>JSON.parse(fs.readFileSync(path.join(root,p),'utf8'));
const git=(...args)=>cp.execFileSync('git',args,{cwd:root,encoding:'utf8'});
const index=fs.existsSync(path.join(root,'index/stable.json'))?read('index/stable.json'):null;
const baselineRef=process.env.VISUALS_BASELINE_REF||(index?.latest?index.versions[index.latest].source_commit:config.migration_ref);
const initial=baselineRef===config.migration_ref;
const metadataAt=ref=>{const names=ref?git('ls-tree','-r','--name-only',ref).split('\n'):fs.readdirSync(path.join(root,'visuals')).map(s=>'visuals/'+s+'/visual.json');if(names.includes('visuals.json'))return JSON.parse(git('show',ref+':visuals.json')).visuals;return Object.fromEntries(names.filter(n=>/^visuals\/[^/]+\/visual.json$/.test(n)).map(n=>[n.split('/')[1],ref?JSON.parse(git('show',ref+':'+n)):read(n)]));};
const previous=metadataAt(baselineRef),current=metadataAt();
const oldLayout=git('ls-tree','--name-only',baselineRef).split('\n').includes('presets');
const loadOld=slug=>JSON.parse(git('show',baselineRef+':'+(oldLayout?'presets/definitions/'+slug+'.definition.json':'visuals/'+slug+'/definition.json')));
const server=http.createServer((req,res)=>{let relative=decodeURIComponent(new URL(req.url,'http://localhost').pathname).slice(1);if(!relative){res.setHeader('Content-Type','text/html');return res.end('<!doctype html><title>Visual regression</title>');}const file=path.resolve(root,relative);if(!file.startsWith(root+path.sep)||!['engine','fixtures'].includes(relative.split('/')[0]))return res.writeHead(404).end();res.setHeader('Content-Type',file.endsWith('.js')?'text/javascript':file.endsWith('.svg')?'image/svg+xml':'application/json');fs.createReadStream(file).on('error',()=>res.writeHead(404).end()).pipe(res);});
async function run(){
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 const browser=await chromium.launch({headless:true,args:['--no-sandbox','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
 const report={browser:browser.version(),recipe:config,baseline_ref:baselineRef,rows:[]};
 try{
  const page=await browser.newPage();page.setDefaultTimeout(120000);
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:'+server.address().port);
  for(const n of ['three.min.js','vrch_pages_scene_spec.js','vrch_pages_scene_contract.js','vrch_pages_scene_signal_bus.js','vrch_pages_scene_source_binding.js','vrch_pages_scene_runtime.js'])await page.addScriptTag({url:'/engine/'+n});
  await page.evaluate(async recipe=>{
   window.recipe=recipe;window.inputs=await(await fetch('/fixtures/synthetic-inputs.json')).json();
   const image=new Image();image.src='/fixtures/test-card.svg';await image.decode();
   const canvas=document.createElement('canvas');canvas.width=256;canvas.height=256;canvas.getContext('2d',{willReadFrequently:true}).drawImage(image,0,0);window.sourceCard=canvas;
   window.renderVisual=async function(definition){
    // Every run has its own renderer, effect, clock, signal history and fixed random seed.
    let seed=123456789;const random=Math.random;Math.random=()=>{seed=(Math.imul(seed,1664525)+1013904223)>>>0;return seed/4294967296;};
    const renderer=new THREE.WebGLRenderer({preserveDrawingBuffer:true,antialias:false});renderer.setPixelRatio(1);renderer.setSize(recipe.width,recipe.height);
    const gl=renderer.getContext(),ext=gl.getExtension('WEBGL_debug_renderer_info');const gpu=ext?gl.getParameter(ext.UNMASKED_RENDERER_WEBGL):gl.getParameter(gl.RENDERER);
    if(!/SwiftShader/i.test(gpu))throw Error('SwiftShader required: '+gpu);
    const scene=new THREE.Scene(),camera=new THREE.Camera();let effect;
    renderer.debug.onShaderError=(gl,program,vs,fs)=>{throw Error('Shader compilation failed: '+gl.getProgramInfoLog(program)+' '+gl.getShaderInfoLog(fs));};
    try{
     const audioV2=definition.signals.some(s=>s.id.startsWith('audio.v2.'));
     const instance={schema_version:1,instance_id:'render-check',scene_ref:{scene_id:definition.scene_id,content_hash:await VrchSceneContract.sha256Ref(definition)},params:Object.fromEntries(definition.parameters.map(p=>[p.key,p.default])),bindings:definition.default_bindings,quality_mode:recipe.quality,modulation_master:{audio:audioV2?{contract_version:2,enabled:true,strength:1,band_gains:{low:1,mid:1,high:1}}:{enabled:true,gain:1}}};
     effect=new VrchSceneRuntime.SceneEffect({definition,instance,renderer,scene,camera,trusted:definition.provenance.kind==='builtin'});
     effect.setSources([sourceCard,sourceCard]);
     const captures=[];
     for(const frame of inputs.frames){
      const values={};for(const s of definition.signals.filter(s=>s.id.startsWith('tracking.')&&s.type!=='texture'))values[s.id]=s.id.includes('confidence')?1:s.id.includes('left_hand_x')?frame.pose.left_hand[0]:.5;
      effect.signalBus.setExternalValues(values);
      for(const s of definition.signals.filter(s=>s.type==='texture'&&s.id.startsWith('tracking.'))){
       if(s.id.endsWith('overlay'))effect.setSignalTexture(s.id,sourceCard);
       else{const width=s.id.includes('face')?512:s.id.includes('hand-')?32:64,height=s.id.includes('hand-')?2:1,data=new Float32Array(width*height*4);for(let i=0;i<width*height;i++){data.set([.5+.2*Math.sin(i*.7+frame.time),.5+.2*Math.cos(i*.9),0,1],i*4);}if(s.id.includes('pose')){data.set(frame.pose.left_hand,15*4);data.set(frame.pose.right_hand,16*4);}effect.setSignalTexture(s.id,{data,width,height});}
      }
      const low=frame.audio['audio.v2.low'],mid=frame.audio['audio.v2.mid'],high=frame.audio['audio.v2.high'];
      effect.prepareFrame(frame.time,{low,mid,high,rms:(low+mid+high)/3,v2_raw_low:low,v2_raw_mid:mid,v2_raw_high:high,v2_raw_energy:(low+mid+high)/3,frequencyData:new Uint8Array(512).fill(Math.round(128*low)),sampleRate:48000});
      renderer.render(scene,camera);
      if(recipe.sample_frames.includes(frame.frame)){const bytes=new Uint8Array(recipe.width*recipe.height*4);gl.readPixels(0,0,recipe.width,recipe.height,gl.RGBA,gl.UNSIGNED_BYTE,bytes);captures.push(Array.from(bytes));}
      const error=gl.getError();if(error!==gl.NO_ERROR)throw Error('WebGL error '+error);
     }
     return {gpu,captures,stages:['main',...(definition.frame?['frame']:[]),...(definition.coarse?['coarse']:[])]};
    }finally{effect?.dispose();renderer.dispose();renderer.forceContextLoss();Math.random=random;}
   };
  },config);
  // Exercise the comparison and driver failure paths with generated definitions.
  const template=read('visuals/bitmap/definition.json');
  const solid=structuredClone(template);solid.signals=[];solid.default_bindings=[];solid.parameters=[];delete solid.ui;solid.runtime.min_sources=0;solid.runtime.max_sources=0;delete solid.frame;delete solid.coarse;solid.fragment.source='void mainImage(out vec4 fragColor, in vec2 fragCoord) { fragColor = vec4(0.2, 0.4, 0.6, 1.0); }';
  const render=d=>page.evaluate(d=>renderVisual(d),d);
  const first=await render(solid),again=await render(solid);assert.deepEqual(first.captures,again.captures,'Fresh identical runs must be pixel exact');
  const changed=structuredClone(solid);changed.fragment.source=changed.fragment.source.replace('0.2','0.8');assert.notDeepEqual(first.captures,(await render(changed)).captures,'Known visual change must be detected');
  const broken=structuredClone(solid);broken.fragment.source=broken.fragment.source.replace('vec4(0.2, 0.4, 0.6, 1.0)','not_a_glsl_function()');await assert.rejects(()=>render(broken),/compilation|undefined|unknown|identifier/i);
  for(const [slug,meta] of Object.entries(current)){
   const start=Date.now(),definition=read('visuals/'+slug+'/definition.json');const rendered=await render(definition);
   const old=previous[slug];let diff=null;
   if(old&&(initial||old.content_hash!==meta.content_hash)){
    let before;try{before=await render(loadOld(slug));}catch(e){throw Error('Engine regression compiling previous '+slug+': '+e.message);}
    diff=rendered.captures.reduce((sum,frame,i)=>sum+frame.filter((v,j)=>v!==before.captures[i][j]).length,0);
    if(diff){const a=old.version.split('.').map(Number),b=meta.version.split('.').map(Number);assert(b[0]>a[0]||(b[0]===a[0]&&b[1]>a[1]),slug+': changed picture requires at least a minor version');}
   }
   report.rows.push({slug,stages:rendered.stages,different_bytes:diff,sample_sha256:rendered.captures.map(b=>crypto.createHash('sha256').update(Buffer.from(b)).digest('hex')),elapsed_ms:Date.now()-start});
   console.log('PASS',slug,diff===null?'compiled':'compared '+diff+' bytes',Date.now()-start+'ms');
  }
  assert.deepEqual(errors,[]);report.gpu=first.gpu;
  fs.mkdirSync(path.join(root,'.test-output'),{recursive:true});fs.writeFileSync(path.join(root,'.test-output/render-report.json'),JSON.stringify(report,null,2)+'\n');
  console.log('PASS render',report.rows.length,report.browser,report.gpu);
 }finally{await browser.close();server.close();}
}
run().catch(e=>{console.error(e);server.close();process.exitCode=1;});
