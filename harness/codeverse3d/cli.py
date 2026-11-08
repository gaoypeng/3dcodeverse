"""``3dcode`` — the command line of every instrument: one subcommand per registered tool, generated from its
typed arguments.

    3dcode <tool> [--workspace DIR] [--round N] [--json] [--<arg> value ...]
    3dcode list · 3dcode doctor [--no-gpu] [--json]

A tool prints its verdict first (PASS / FAIL / ERROR), then its numbers and the absolute paths of the PNGs it
wrote.  Exit 0 = the tool ran (a FAIL verdict is still 0); 2 = it could not run or the arguments were wrong.
``--json`` prints the Observation as JSON instead.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import types
import typing
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from codeverse3d import __version__
from codeverse3d.spatial.observe import fmt_numbers
from codeverse3d.spatial.registry import Observation, ToolContext, ToolDef, list_tools
from codeverse3d.spatial.tool_common import spec_dict
from codeverse3d.workspace import Workspace

PROG = "3dcode"
EXIT_FAILED = 2
MAX_NUMBERS_CHARS = 1500


def cli_name(tool: str) -> str:
    """``validate_plan`` → ``validate-plan`` (the underscore spelling stays an alias)."""
    return tool.replace("_", "-")


# --------------------------------------------------------------------------- arguments from the pydantic model
def _kv_float(text: str) -> tuple[str, float]:
    key, sep, val = text.partition("=")
    if not sep or not key.strip():
        raise argparse.ArgumentTypeError(f"expected key=value, got {text!r}")
    try:
        return key.strip(), float(val)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"{key}: {val!r} is not a number") from e


def _unwrap(annotation: Any) -> Any:
    """``X | None`` / ``Optional[X]`` → ``X``."""
    if typing.get_origin(annotation) in (typing.Union, types.UnionType):
        inner = [a for a in typing.get_args(annotation) if a is not type(None)]
        return inner[0] if len(inner) == 1 else annotation
    return annotation


def add_model_arguments(parser: argparse.ArgumentParser, model: type[BaseModel]) -> None:
    """One ``--field`` option per model field: bools as ``--x/--no-x``, lists repeatable, ``dict[str, float]`` as
    repeatable ``key=value``."""
    for name, field in model.model_fields.items():
        flag = "--" + name.replace("_", "-")
        ann = _unwrap(field.annotation)
        origin = typing.get_origin(ann)
        help_text = field.description or ""
        required = field.is_required()
        if ann is bool:
            parser.add_argument(flag, dest=name, action=argparse.BooleanOptionalAction, default=argparse.SUPPRESS,
                                help=help_text)
        elif origin is list:
            (item,) = typing.get_args(ann) or (str,)
            parser.add_argument(flag, dest=name, action="append", type=item if item in (int, float, str) else str,
                                default=argparse.SUPPRESS, required=required, metavar=item.__name__.upper(),
                                help=help_text + " (repeatable)")
        elif origin is dict:
            parser.add_argument(flag, dest=name, action="append", type=_kv_float, default=argparse.SUPPRESS,
                                required=required, metavar="KEY=VALUE", help=help_text + " (repeatable)")
        else:
            typ = ann if ann in (int, float, str) else str
            parser.add_argument(flag, dest=name, type=typ, default=argparse.SUPPRESS, required=required,
                                metavar=typ.__name__.upper(), help=help_text)


def model_arguments(ns: argparse.Namespace, model: type[BaseModel]) -> dict[str, Any]:
    """The namespace's tool arguments as the dict the args model takes (dict fields merged from key=value pairs)."""
    out: dict[str, Any] = {}
    for name, field in model.model_fields.items():
        if not hasattr(ns, name):
            continue
        value = getattr(ns, name)
        if typing.get_origin(_unwrap(field.annotation)) is dict and isinstance(value, list):
            value = dict(value)
        out[name] = value
    return out


# --------------------------------------------------------------------------- the parser
def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--workspace", "-w", default=None, metavar="DIR",
                        help="the project directory (default: $C3D_WORKSPACE, else the current directory)")
    parser.add_argument("--round", dest="round_index", type=int, default=0, metavar="N",
                        help="the round this call belongs to (judge/rNN, artifacts/renders/rNN); default 0")
    parser.add_argument("--json", dest="as_json", action="store_true", help="print the result as JSON")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=PROG, description="3dcodeverse instruments: build, measure, render and check "
                                     "the 3D code in a project workspace.")
    parser.add_argument("--version", action="version", version=f"{PROG} {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<tool>")
    sub.required = True
    p_list = sub.add_parser("list", help="list every tool with its arguments")
    p_list.add_argument("--json", dest="as_json", action="store_true")
    p_doc = sub.add_parser("doctor", help="check python deps, Blender, node / three / puppeteer, the GPU probe")
    p_doc.add_argument("--gpu", action=argparse.BooleanOptionalAction, default=True, help="probe headless Chrome WebGL")
    p_doc.add_argument("--json", dest="as_json", action="store_true")
    for t in list_tools():
        p = sub.add_parser(cli_name(t.name), aliases=[t.name] if "_" in t.name else [],
                           help=f"({t.cost_hint}) {t.description}", description=t.description)
        _common(p)
        add_model_arguments(p, t.args_model)
        p.set_defaults(tool=t)
    return parser


def resolve_workspace(arg: str | None) -> Workspace:
    root = Path(arg or os.environ.get("C3D_WORKSPACE") or Path.cwd()).expanduser()
    return Workspace(root)


def build_context(ws: Workspace, round_index: int) -> ToolContext:
    """ToolContext for ``ws``; track / language come from spec.json, re-read on every call."""
    ctx = ToolContext(workspace=ws, round_index=round_index)
    spec = spec_dict(ctx)
    ctx.track = str(spec.get("track", ""))
    ctx.language = str(spec.get("language", ""))
    return ctx


# --------------------------------------------------------------------------- output
def format_observation(obs: Observation) -> str:
    lines = [obs.text.rstrip()]
    if obs.numbers:
        nums = json.dumps(obs.numbers, default=str)
        lines.append("numbers: " + (nums if len(nums) <= MAX_NUMBERS_CHARS else fmt_numbers(obs.numbers)))
    if obs.images:
        lines.append("images:")
        lines += [f"  {Path(p).resolve()}" for p in obs.images]
    return "\n".join(lines)


def run_tool(tdef: ToolDef, ns: argparse.Namespace) -> int:
    ws = resolve_workspace(ns.workspace)
    if not ws.root.is_dir():
        if tdef.name == "init":
            ws.root.mkdir(parents=True, exist_ok=True)
        else:
            print(f"ERROR: workspace not found: {ws.root}", file=sys.stderr)
            return EXIT_FAILED
    ctx = build_context(ws, ns.round_index)
    obs = tdef.call(ctx, model_arguments(ns, tdef.args_model))
    if ns.as_json:
        print(json.dumps(obs.model_dump(mode="json"), indent=1, default=str))
    else:
        print(format_observation(obs))
    return EXIT_FAILED if obs.failed else 0


def list_command(as_json: bool) -> int:
    tools = list_tools()
    if as_json:
        print(json.dumps([{"name": cli_name(t.name), "cost": t.cost_hint, "description": t.description,
                           "tracks": list(t.tracks), "languages": list(t.languages), "schema": t.schema()} for t in tools],
                         indent=1))
        return 0
    for t in tools:
        scope = ""
        if t.tracks:
            scope = f" [tracks: {', '.join(t.tracks)}]"
        elif t.languages:
            scope = f" [languages: {', '.join(t.languages)}]"
        print(f"{cli_name(t.name)} ({t.cost_hint}){scope}: {t.description}")
        props = t.schema().get("properties", {})
        req = set(t.schema().get("required", []))
        for k, v in props.items():
            mark = "required" if k in req else f"default={v.get('default')!r}"
            print(f"    --{k.replace('_', '-')}: {v.get('type', 'any')} — {v.get('description', '')} ({mark})")
    return 0


def doctor_command(gpu: bool, as_json: bool) -> int:
    from codeverse3d.doctor import format_rows, run_doctor

    rows = run_doctor(gpu=gpu)
    print(json.dumps([{"check": c, "status": s, "detail": d} for c, s, d in rows], indent=1) if as_json else format_rows(rows))
    return 1 if any(s == "FAIL" for _, s, _ in rows) else 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    ns = parser.parse_args(argv)
    if ns.command == "list":
        return list_command(ns.as_json)
    if ns.command == "doctor":
        return doctor_command(ns.gpu, ns.as_json)
    return run_tool(ns.tool, ns)


if __name__ == "__main__":
    sys.exit(main())
