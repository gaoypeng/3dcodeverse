#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {parseArgs} from 'node:util';
import {createRequire} from 'node:module';
const require = createRequire(import.meta.url);
const {serveDirs} = require('../../runtime_js/serve.cjs');
const here = path.dirname(fileURLToPath(import.meta.url));
const {values} = parseArgs({options:{out:{type:'string', default:path.join(here,'output')}}});
const root = path.resolve(values.out);
if (!fs.existsSync(path.join(root,'index.html'))) throw new Error('Run build.py first');
const server = await serveDirs({root, routes:{'/':{body:fs.readFileSync(path.join(root,'index.html'),'utf8')}}});
console.log(`Graphics Lab: ${server.base}/`);
for (const signal of ['SIGINT','SIGTERM']) process.on(signal, async()=>{await server.close();process.exit(0);});
