"""`3dcode` CLI — contributor-side commands to validate and upload (code -> 3D) projects.

    3dcode config set --endpoint ... --access-key-id ... --secret-access-key ...
    3dcode validate ./data
    3dcode push ./data --source yourname
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

from . import __version__, registry
from .config import load_config, save_config
from .hashing import project_hashes
from .schema import META_NAME, find_projects, merge_preserved, validate_project

app = typer.Typer(add_completion=False, help="3DCodeVerse data toolkit.")
config_app = typer.Typer(help="Manage R2 endpoint / token / default source.")
app.add_typer(config_app, name="config")


@app.command()
def version():
    """Print the toolkit version."""
    typer.echo(f"3dcode {__version__}")


@config_app.command("set")
def config_set(
    endpoint: str = typer.Option(None),
    bucket: str = typer.Option(None),
    access_key_id: str = typer.Option(None),
    secret_access_key: str = typer.Option(None),
    source: str = typer.Option(None, help="default source folder for your uploads"),
    api: str = typer.Option(None, help="registry API base (default 3dcodebench.com)"),
    contrib_token: str = typer.Option(None, help="shared contributor token for the registry"),
    blender_5_0: str = typer.Option(None, help="local Blender 5.0 binary (for 3dcode exec)"),
    blender_5_1: str = typer.Option(None, help="local Blender 5.1 binary"),
    openscad: str = typer.Option(None, help="local OpenSCAD binary/AppImage (.scad)"),
    freecadcmd: str = typer.Option(None, help="local freecadcmd binary (FreeCAD)"),
    core_dir: str = typer.Option(None, help="admin canonical store dir (for 3dcode ingest)"),
):
    """Write R2 settings to ~/.config/3dcode/config.toml (chmod 600)."""
    path = save_config({
        "endpoint": endpoint, "bucket": bucket, "access_key_id": access_key_id,
        "secret_access_key": secret_access_key, "source": source,
        "api": api, "contrib_token": contrib_token,
        "blender_5_0": blender_5_0, "blender_5_1": blender_5_1,
        "openscad": openscad, "freecadcmd": freecadcmd, "core_dir": core_dir,
    })
    typer.echo(f"saved -> {path}")


@config_app.command("show")
def config_show():
    """Show the active config (secret masked)."""
    c = load_config()
    secret = (c.secret_access_key[:4] + "…") if c.secret_access_key else "(unset)"
    for k, v in {"endpoint": c.endpoint, "bucket": c.bucket,
                 "access_key_id": c.access_key_id or "(unset)",
                 "secret_access_key": secret, "source": c.source or "(unset)"}.items():
        typer.echo(f"  {k:18} {v}")


def _iter(root: Path, source: str):
    projects = find_projects(root)
    if not projects:
        typer.echo(f"no projects found under {root}", err=True)
        raise typer.Exit(1)
    for d in projects:
        errors, warnings, meta = validate_project(d, source)
        yield d, errors, warnings, meta


@app.command()
def validate(path: Path = typer.Argument(..., exists=True),
             source: str = typer.Option(None, help="source name (defaults to config)")):
    """Validate project(s) locally — no upload."""
    source = source or load_config().source or "unknown"
    bad = 0
    for d, errors, warnings, _ in _iter(path, source):
        if errors:
            bad += 1
            typer.secho(f"✗ {d.name}: {'; '.join(errors)}", fg="red")
        else:
            tag = typer.style("✓", fg="green")
            typer.echo(f"{tag} {d.name}" + (f"  ⚠ {'; '.join(warnings)}" if warnings else ""))
    if bad:
        raise typer.Exit(1)


@app.command()
def check(path: Path = typer.Argument(..., exists=True),
          source: str = typer.Option(None, help="source name (defaults to config)")):
    """Validate + fingerprint locally, and flag duplicates vs the existing corpus — no upload."""
    cfg = load_config()
    source = source or cfg.source or "unknown"
    index = registry.fetch_index(cfg)
    n = dups = 0
    for d, errors, warnings, meta in _iter(path, source):
        n += 1
        if errors:
            typer.secho(f"✗ {d.name}: {'; '.join(errors)}", fg="red")
            continue
        meta["dedup"] = project_hashes(d, meta)
        hits = registry.dedup_matches(meta, index)
        if hits:
            dups += 1
            for ex, why in hits:
                typer.secho(f"⚠ {d.name}: {why} as {ex}", fg="yellow")
        else:
            tail = f"  ⚠ {'; '.join(warnings)}" if warnings else ""
            typer.secho(f"✓ {d.name}: unique{tail}", fg="green")
    typer.echo(f"\nchecked {n} · {dups} with potential duplicates · index size {len(index)}")


@app.command()
def anomalies(path: Path = typer.Argument(..., exists=True),
              source: str = typer.Option(None, help="source name (defaults to config)")):
    """Distribution-based outlier scan — flag projects whose code length / char count is an outlier."""
    source = source or load_config().source or "unknown"
    rows = []
    for d, errors, _, meta in _iter(path, source):
        if errors or not meta:
            continue
        chars = len((d / meta["code"]["file"]).read_text(errors="replace"))
        rows.append((d.name, meta["code"]["lines"], chars))
    if len(rows) < 4:
        typer.echo(f"need >= 4 valid projects for a distribution (got {len(rows)})")
        return

    def bounds(vals: list[int]) -> tuple[float, float]:
        s = sorted(vals)
        q1, q3 = s[len(s) // 4], s[(3 * len(s)) // 4]
        iqr = q3 - q1
        return q1 - 1.5 * iqr, q3 + 1.5 * iqr

    lo_l, hi_l = bounds([r[1] for r in rows])
    lo_c, hi_c = bounds([r[2] for r in rows])
    flagged = 0
    for name, lines, chars in rows:
        why = []
        if lines < lo_l or lines > hi_l:
            why.append(f"lines={lines} (typical {int(max(0, lo_l))}–{int(hi_l)})")
        if chars < lo_c or chars > hi_c:
            why.append(f"chars={chars} (typical {int(max(0, lo_c))}–{int(hi_c)})")
        if why:
            flagged += 1
            typer.secho(f"⚠ {name}: {'; '.join(why)}", fg="yellow")
    typer.echo(f"\nscanned {len(rows)} · {flagged} length/char outliers")


@app.command("exec")
def exec_run(path: Path = typer.Argument(..., exists=True),
             source: str = typer.Option(None, help="source name (defaults to config)"),
             dialect: str = typer.Option(None, help="override code dialect (e.g. cadquery)"),
             write: bool = typer.Option(True, help="write the result into each meta.json")):
    """Run each project's code in its dialect runtime(s); record pass/fail in meta.json.

    Runs LOCALLY in your own runtimes (configure Blender via `3dcode config set
    --blender-5-0 ... --blender-5-1 ...`); nothing is uploaded. Pass = error-free AND a non-empty mesh.
    """
    from .dialects import run_dialect
    cfg = load_config()
    source = source or cfg.source or "unknown"
    npass = nfail = 0
    for d, errors, _, meta in _iter(path, source):
        if errors:
            typer.secho(f"✗ {d.name}: {'; '.join(errors)}", fg="red")
            continue
        merge_preserved(d, meta)                     # don't clobber license / provenance
        if dialect:
            meta["dialect"] = dialect
        res = run_dialect(meta["dialect"], d, meta, cfg)
        meta["exec"] = res
        if write:
            (d / META_NAME).write_text(json.dumps(meta, indent=2, ensure_ascii=False))
        parts, passed = [], True
        for rt, r in res.items():
            st = r.get("status")
            extra = ""
            if st == "ok":
                if r.get("verts"):
                    extra = f"({r['verts']}v)"
                elif r.get("volume") is not None:
                    extra = f"(vol {r['volume']})"
            parts.append(f"{rt}={st}{extra}")
            if st not in ("ok", "n/a", "unsupported"):
                passed = False
        typer.secho(f"{'✓' if passed else '✗'} {d.name}: {'  '.join(parts)}",
                    fg="green" if passed else "red")
        npass += passed
        nfail += not passed
    typer.echo(f"\nexec: {npass} passed · {nfail} failed")


@app.command()
def push(path: Path = typer.Argument(..., exists=True),
         source: str = typer.Option(None, help="source name (defaults to config)"),
         owner: str = typer.Option(None, help="who is responsible for this data"),
         dialect: str = typer.Option(None, help="override code dialect"),
         dry_run: bool = typer.Option(False, "--dry-run", help="validate + hash, do not upload"),
         no_dedup: bool = typer.Option(False, "--no-dedup", help="skip the duplicate check"),
         no_register: bool = typer.Option(False, "--no-register", help="upload but don't register in the registry")):
    """Validate -> hash -> dedup-check -> write meta.json -> upload to R2 -> register."""
    cfg = load_config()
    source = source or cfg.source
    if not source:
        typer.secho("no --source and no default source in config", fg="red", err=True)
        raise typer.Exit(1)
    contributor = owner or source

    r2 = None
    if not dry_run:
        from .storage import R2  # imported lazily so validate/dry-run need no creds
        r2 = R2(cfg)
    index = [] if no_dedup else registry.fetch_index(cfg)

    up = skip = warn = dup = 0
    for d, errors, warnings, meta in _iter(path, source):
        if errors:
            skip += 1
            typer.secho(f"✗ {d.name}: {'; '.join(errors)} — skipped", fg="red")
            continue
        if dialect:
            meta["dialect"] = dialect
        merge_preserved(d, meta)                     # keep exec / license / provenance from disk
        meta["owner"] = owner or meta.get("owner", "")
        meta["contributor"] = contributor
        meta["dedup"] = project_hashes(d, meta)
        matches = registry.dedup_matches(meta, index)
        # validation report — travels with the project so the admin Inbox can review it
        meta["report"] = {
            "warnings": warnings,
            "duplicates": [{"of": ex, "why": why} for ex, why in matches],
            "exec": meta.get("exec"),
            "files": sorted(p.relative_to(d).as_posix() for p in d.rglob("*") if p.is_file()),
        }
        (d / META_NAME).write_text(json.dumps(meta, indent=2, ensure_ascii=False))

        for ex, why in matches:
            dup += 1
            typer.secho(f"⚠ {d.name}: {why} as {ex}", fg="yellow")
        if warnings:
            warn += 1
            typer.secho(f"⚠ {d.name}: {'; '.join(warnings)}", fg="yellow")
        if dry_run:
            up += 1
            typer.echo(f"  (dry-run) {meta['id']} ready — {len(meta['dedup'])} fingerprints")
            continue
        result = r2.upload_tree(d, f"{source}/{d.name}")
        up += 1
        typer.secho(f"↑ {meta['id']}  ({result.files} files, {result.bytes/1e6:.1f} MB)", fg="green")
        if not no_register:
            resp = registry.register(cfg, meta)
            if resp.get("error"):
                typer.secho(f"  ! registry: {resp['error']}", fg="red")

    typer.echo(f"\n{'validated' if dry_run else 'uploaded'}: {up} · dup-flagged: {dup} · warn: {warn} · skipped: {skip}")


@app.command()
def render(path: Path = typer.Argument(..., exists=True),
          source: str = typer.Option(None, help="source name (defaults to config)"),
          dialect: str = typer.Option(None, help="override code dialect (e.g. cadquery)"),
          mode: str = typer.Option("white", help="white | textured")):
    """Render each project's code LOCALLY into renders/ (white clay or textured) — no upload.

    Runs the code in your local Blender (config --blender-5-0) and writes view_00..03.png +
    thumb.png. Then `3dcode push` picks up the renders (modality + phash dedup + Inbox preview).
    """
    from .dialects import render_dialect
    cfg = load_config()
    source = source or cfg.source or "unknown"
    n = fail = 0
    for d, errors, _, meta in _iter(path, source):
        if errors:
            typer.secho(f"✗ {d.name}: {'; '.join(errors)}", fg="red"); continue
        if dialect:
            meta["dialect"] = dialect
        res = render_dialect(meta["dialect"], d, meta, cfg, mode)
        st = res.get("status")
        if st == "ok":
            n += 1
            typer.secho(f"🖼 {d.name}: {len(res.get('images', []))} views → renders/", fg="green")
        else:
            fail += 1
            typer.secho(f"✗ {d.name}: {st}" + (f" — {res['error']}" if res.get("error") else ""), fg="red")
    typer.echo(f"\nrendered: {n} · failed: {fail}")


@app.command()
def grid(path: Path = typer.Argument(..., exists=True),
         out: Path = typer.Option(None, help="output PNG (default <dir>/_grid.png)"),
         cols: int = typer.Option(6, help="columns")):
    """Compose a contact-sheet PNG of a source's project thumbnails (quick visual review)."""
    from .grid import make_grid
    projects = find_projects(path)
    if not projects:
        typer.secho(f"no projects under {path}", fg="red", err=True)
        raise typer.Exit(1)
    dest = out or (path / "_grid.png")
    try:
        n = make_grid(projects, dest, cols=cols)
    except ImportError:
        typer.secho("grid needs Pillow — install 3dcode[dedup]", fg="red", err=True)
        raise typer.Exit(1)
    if n == 0:
        typer.secho("no thumbnails found — run `3dcode render` first?", fg="yellow")
    else:
        typer.secho(f"grid: {n} thumbnails → {dest}", fg="green")


@app.command()
def ingest(project_id: str = typer.Argument(None, help="<source>/<project> to ingest (or use --all-approved)"),
           all_approved: bool = typer.Option(False, "--all-approved", help="ingest every approved project"),
           keep_staging: bool = typer.Option(False, "--keep-staging", help="don't delete from R2 after pulling")):
    """ADMIN (lab-side): pull approved projects R2 → core dir, mark ingested, clear staging."""
    cfg = load_config()
    from .storage import R2
    r2 = R2(cfg)
    core = Path(cfg.core_dir)

    if all_approved:
        ids = [a["id"] for a in registry.fetch_approved(cfg)]
    elif project_id:
        ids = [project_id]
    else:
        typer.secho("give a <source>/<project> or --all-approved", fg="red", err=True)
        raise typer.Exit(1)
    if not ids:
        typer.echo("nothing approved to ingest")
        return

    done = fail = 0
    for pid in ids:
        try:
            dest = core / pid                          # core/<source>/<project>
            res = r2.download_tree(pid, dest)
            if res.files == 0:
                typer.secho(f"✗ {pid}: nothing in staging", fg="red"); fail += 1; continue
            meta_path = dest / META_NAME
            if meta_path.exists():
                meta = json.loads(meta_path.read_text())
            else:
                src, _, proj = pid.partition("/")
                meta = {"id": pid, "source": src, "project": proj}
            meta["status"] = "ingested"
            resp = registry.register(cfg, meta)
            if resp.get("error"):
                typer.secho(f"  ! mark-ingested: {resp['error']}", fg="yellow")
            if not keep_staging:
                r2.delete_prefix(pid.rstrip("/") + "/")
            typer.secho(f"↓ {pid}  ({res.files} files, {res.bytes / 1e6:.1f} MB) → {dest}", fg="green")
            done += 1
        except Exception as e:
            typer.secho(f"✗ {pid}: {e}", fg="red"); fail += 1
    typer.echo(f"\ningested: {done} · failed: {fail}")


if __name__ == "__main__":
    app()
