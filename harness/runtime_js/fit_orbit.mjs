#!/usr/bin/env node
/**
 * Fit the overview orbit rig to a census WITHOUT a browser (scene_blender, D1): the same
 * `framingBox` + `fitOrbitCameras` render_scene.mjs runs in-page, on the census the build's
 * probe already wrote — one implementation of the fitting, so a Blender render frames its
 * overviews exactly as the three.js driver would.
 *
 *   node fit_orbit.mjs --census <census.json> --orbit-views '<json [{name,azimuth,elevation}]>'
 *        [--bounds '{"min":[x,y,z],"max":[x,y,z]}'|none] [--width 1024] [--height 576]
 *
 * Last stdout line: {ok, framing_bbox, cameras: [{name, position, lookAt, fov, kind:'orbit', noFog}]}
 * (Y-up GLB frame).  Exit 0 ok, 2 bad input.
 */

import fs from 'node:fs';
import { fail, finish, parseCli, readJsonArg } from './lib/cli.mjs';
import { fitOrbitCameras, framingBox } from './lib/orbit.mjs';

const args = parseCli({
  census: {}, 'orbit-views': { default: '[]' }, bounds: { default: 'none' },
  width: { default: '1024' }, height: { default: '576' },
});

try {
  if (!args.census) throw new Error('--census is required');
  const census = JSON.parse(fs.readFileSync(args.census, 'utf8'));
  const views = readJsonArg(args['orbit-views'], 'orbit-views') || [];
  const bounds = args.bounds && args.bounds !== 'none' ? readJsonArg(args.bounds, 'bounds') : null;
  const bbox = framingBox(census, bounds);
  const aspect = parseInt(args.width, 10) / parseInt(args.height, 10);
  const cameras = fitOrbitCameras(bbox, views, { aspect, groundY: census.ground_y });
  finish({ ok: true, framing_bbox: bbox, cameras });
} catch (e) {
  fail(`fit_orbit failed: ${e.message}`);
}
