"""Render controlled library comparisons from an immutable baseline workspace set.

Each baseline/<case>/src tree is copied twice. Only the named library module is
replaced in the second copy; scene, camera, lighting and all other modules stay
byte-identical. No provider calls or external assets are used.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from build import HARNESS, HERE, LIB

CASES = (
    ('ocean', 'ocean.js', 'ocean', 'Ocean', 'Directional waves, filtered reflections and broken whitecaps.'),
    ('stream', 'stream.js', 'stream', 'Stream', 'Refraction, water-column absorption, ripple lighting and seated cobbles.'),
    ('rocks', 'rock.js', 'granite', 'Rock', 'Fracture geometry, recessed weathering and mineral scale.'),
    ('meadow', 'meadow.js', 'blades', 'Meadow', 'Leaf growth forms, habitat variation and shadowed transmission.'),
)


def hashes(root: Path) -> dict[str, str]:
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(root.rglob('*.js'))}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--out', type=Path, default=HERE / 'output')
    args = parser.parse_args()
    out = args.out.resolve()
    destination = out / 'comparison'
    records = []
    for key, module, camera, title, note in CASES:
        record = {'case': key, 'module': module, 'camera': camera, 'time': 0,
                  'title': title, 'note': note, 'variants': {}}
        baseline = args.baseline.resolve() / key / 'src'
        if not (baseline / 'scene.js').is_file():
            raise FileNotFoundError(f'Missing baseline workspace: {baseline}')
        for variant in ('before', 'after'):
            workspace = destination / 'workspaces' / key / variant
            source = workspace / 'src'
            if source.exists():
                shutil.rmtree(source)
            shutil.copytree(baseline, source)
            if variant == 'after':
                shutil.copy2(LIB / module, source / 'lib' / module)
            render = destination / 'renders' / key / variant
            print(f'Comparing {key}: {variant}', flush=True)
            subprocess.run([
                'node', str(HARNESS / 'runtime_js/render_scene.mjs'),
                '--ws', str(workspace), '--out', str(render), '--times', '0',
                '--width', '1280', '--height', '720', '--fps-seconds', '0',
                '--no-settle', '--timeout-ms', '180000',
            ], check=True, cwd=HARNESS, timeout=200)
            frame = render / f'{camera}_t0.png'
            image = destination / f'{key}-{variant}.png'
            shutil.copy2(frame, image)
            record['variants'][variant] = {
                'image': str(image.relative_to(out)), 'source_sha256': hashes(source),
                'metrics': str((render / 'metrics.json').relative_to(out)),
            }
        before = record['variants']['before']['source_sha256']
        after = record['variants']['after']['source_sha256']
        differences = [name for name in sorted(before.keys() | after.keys()) if before.get(name) != after.get(name)]
        if differences != [f'lib/{module}']:
            raise ValueError(f'{key}: expected exactly one changed module, found {differences}')
        records.append(record)
    (destination / 'manifest.json').write_text(json.dumps(records, indent=2))
    cards = '\n'.join(f'''<section><h2>{record['title']}</h2><p>{record['note']}</p>
<div class="compare" style="--split:50%">
<img src="{record['variants']['before']['image']}" alt="{record['title']} before refinement">
<img class="after" src="{record['variants']['after']['image']}" alt="{record['title']} after library refinement">
<span class="divider"></span><span class="label old">Before</span><span class="label new">After</span>
</div><label class="control">Reveal refinement <input type="range" min="0" max="100" value="50" aria-label="{record['title']} comparison position"></label>
<p class="detail">Only <code>{record['module']}</code> changed. Camera, scene, lighting and time are identical.
<a href="{record['variants']['before']['image']}">Before image</a> · <a href="{record['variants']['after']['image']}">After image</a></p></section>'''
                      for record in records)
    (out / 'comparison.html').write_text('''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Graphics Lab · Refinement comparison</title><style>
*{box-sizing:border-box}body{margin:0;background:#10191c;color:#e5e9de;font:16px/1.5 system-ui,sans-serif}
main{max-width:1280px;margin:auto;padding:36px 24px 80px}a{color:#bdd8bb}h1{font-size:clamp(32px,5vw,64px);line-height:1.1;margin:36px 0 18px}
h2{font-size:28px;margin:0}p{color:#aab8b5;max-width:900px}.intro{margin-bottom:48px}section{margin:0 0 60px}
.compare{position:relative;aspect-ratio:16/9;overflow:hidden;border:1px solid #364244;background:#1e2a2e}
.compare img{position:absolute;inset:0;width:100%;height:100%;object-fit:contain}.after{clip-path:inset(0 0 0 var(--split))}
.divider{position:absolute;left:var(--split);height:100%;width:2px;background:#e5e9de;transform:translateX(-1px)}
.label{position:absolute;top:14px;padding:5px 10px;background:#10191cbb;font-size:13px}.old{left:14px}.new{right:14px}
.control{display:flex;align-items:center;gap:20px;margin-top:16px}input{flex:1;accent-color:#b6d3a7;min-width:80px}.detail{font-size:13px}code{color:#e5e9de}
</style></head><body><main><a href="./">← Graphics Lab</a><div class="intro"><h1>Same scene. Refined library.</h1>
<p>Drag each slider to inspect actual GPU renders. These comparisons isolate the library change; the main gallery also includes revised scene composition. Images use the production renderer and postprocessing, with no painted or generated image layers.</p>
<a href="comparison/manifest.json">Source hashes and render records</a></div>
''' + cards + '''</main><script>
document.querySelectorAll('input').forEach(input=>input.addEventListener('input',()=>{
 input.closest('section').querySelector('.compare').style.setProperty('--split',input.value+'%');
}));
</script></body></html>''')
    print(f'Comparison: {out / "comparison.html"}')


if __name__ == '__main__':
    main()
