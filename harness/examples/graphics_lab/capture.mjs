#!/usr/bin/env node
/** Deterministic frames from the production scene host, encoded as H.264. */
import fs from 'node:fs';
import path from 'node:path';
import {spawn} from 'node:child_process';
import {parseArgs} from 'node:util';
import {createHash} from 'node:crypto';
import {openHost} from '../../runtime_js/lib/host_page.mjs';
const {values:a} = parseArgs({options:{ws:{type:'string'},out:{type:'string'},
  ffmpeg:{type:'string',default:'ffmpeg'},width:{type:'string',default:'1280'},
  height:{type:'string',default:'720'},seconds:{type:'string',default:'6'},
  fps:{type:'string',default:'24'},camera:{type:'string',default:'0'}}});
if (!a.ws || !a.out) throw new Error('--ws and --out required');
if (!/\.mp4$/i.test(a.out)) throw new Error('--out must end in .mp4');
const fps = Number(a.fps), count = Math.round(Number(a.seconds) * fps);
if (!(fps>0 && fps<=60 && count>0 && count<=3600)) throw new Error('Invalid capture duration/fps');
for (const size of [Number(a.width), Number(a.height)]) {
  if (!Number.isInteger(size) || size < 2 || size > 8192 || size % 2) {
    throw new Error('H.264 width and height must be even integers from 2 to 8192');
  }
}
function sourceHashes() {
  const root=path.join(path.resolve(a.ws),'src'), hashes={};
  function visit(directory) {
    for (const entry of fs.readdirSync(directory,{withFileTypes:true}).sort((x,y)=>x.name.localeCompare(y.name))) {
      const file=path.join(directory,entry.name);
      if(entry.isDirectory()) visit(file);
      else if(entry.isFile() && /\.(?:m?js|glsl|vert|frag)$/.test(entry.name)) {
        hashes[path.relative(root,file).split(path.sep).join('/')]=createHash('sha256').update(fs.readFileSync(file)).digest('hex');
      }
    }
  }
  visit(root);return hashes;
}
const capturedSource=sourceHashes();
const host = await openHost(a.ws,{width:Number(a.width),height:Number(a.height),
  gpu:'auto',post:true,settle:false,createSceneTimeoutMs:60000});
let encoder;
try {
  if (!host.boot.ok) throw new Error(host.boot.error);
  const camera = host.boot.cameras[Number(a.camera)];
  if (!camera) throw new Error('Camera not found');
  fs.mkdirSync(path.dirname(path.resolve(a.out)),{recursive:true});
  encoder = spawn(a.ffmpeg,['-y','-loglevel','error','-f','image2pipe','-vcodec','png',
    '-r',String(fps),'-i','-','-an','-c:v','libx264','-crf','18','-pix_fmt','yuv420p',
    '-movflags','+faststart',a.out],{stdio:['pipe','ignore','pipe']});
  let stderr=''; encoder.stderr.on('data',b=>{stderr=(stderr+b).slice(-4000);});
  let encoderError;
  encoder.on('error', error => { encoderError = error; });
  encoder.stdin.on('error', error => { encoderError = error; });
  const done = new Promise(resolve => encoder.once('close', resolve));
  const timings=[];
  const referenceFrames=[];
  const referenceIndices=new Set([0,Math.floor(count/3),Math.floor(count*2/3)]);
  const referenceDirectory=path.join(path.dirname(path.resolve(a.out)),
    'capture_frames',path.basename(a.out,'.mp4'));
  fs.mkdirSync(referenceDirectory,{recursive:true});
  for (let i=0;i<count;i++) {
    const frame=await host.page.evaluate((spec,t)=>window.__c3v.renderAt(spec,t),camera,i/fps);
    timings.push(frame.ms);
    const png=Buffer.from(frame.dataUrl.split(',')[1],'base64');
    if(referenceIndices.has(i)) {
      const file=path.join(referenceDirectory,`frame_${String(i).padStart(4,'0')}.png`);
      fs.writeFileSync(file,png);
      referenceFrames.push({index:i,time_s:i/fps,
        path:path.relative(path.dirname(path.resolve(a.out)),file).split(path.sep).join('/'),
        sha256:createHash('sha256').update(png).digest('hex')});
    }
    if (encoderError) throw encoderError;
    await new Promise((resolve, reject) => {
      encoder.stdin.write(png, error => error ? reject(error) : resolve());
    });
  }
  encoder.stdin.end();
  const code=await done;
  if (encoderError) throw encoderError;
  if (code!==0) throw new Error(`ffmpeg exited ${code}: ${stderr}`);
  if (host.errors.console.length || host.errors.page.length || host.errors.shader_console.length) {
    throw new Error(JSON.stringify(host.errors));
  }
  if(JSON.stringify(capturedSource)!==JSON.stringify(sourceHashes())) throw new Error('Scene source changed during capture');
  const summary={ok:true,frames:count,fps,seconds:count/fps,camera:camera.name,
    gpu:host.gpu,renderer:host.boot.renderer,width:Number(a.width),height:Number(a.height),
    console_errors:host.errors.console,shader_errors:host.errors.shader_console,
    source_sha256:capturedSource,
    video_sha256:createHash('sha256').update(fs.readFileSync(a.out)).digest('hex'),
    reference_frames:referenceFrames,
    render_ms_mean:timings.reduce((s,t)=>s+t,0)/count};
  fs.writeFileSync(a.out.replace(/\.mp4$/i,'.capture.json'),JSON.stringify(summary,null,2));
  console.log(JSON.stringify({...summary,source_sha256:undefined,source_files:Object.keys(capturedSource).length}));
} finally {
  if (encoder && encoder.exitCode===null) encoder.kill();
  await host.close();
}
