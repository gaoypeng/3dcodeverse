"""Tool set for the in-process ``api-agent``: workspace-confined file tools + shell + spatial registry.

Every tool returns a :class:`ToolOutcome` (text, images, is_error) and never
raises.  Paths are resolved inside the workspace; writes are restricted to
``write_roots``.  ``run_shell`` is a *policy filter*, not an OS sandbox: only an
allow-listed program, no shell operators, no inline-code flags (``python -c``,
``node -e`` …, ``-m`` only for a few stdlib modules), every path-like argument
must resolve inside the workspace, and the child runs under the watchdog with a
minimal environment (no host secrets).  Trust model = cooperative agent — the
harness executes the agent's own source files anyway (same as the CLI backends).
"""

from __future__ import annotations

import fnmatch
import json
import os
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from codeverse.agents.watchdog import run_with_watchdog
from codeverse.config import get_settings
from codeverse.contracts.chat import ImagePart, ToolSpec
from codeverse.workspace import Workspace

READ_CAP_CHARS = 60_000
SHELL_OUT_CAP = 6_000
SHELL_TIMEOUT_S = 180.0
SHELL_ALLOW = ("node", "python", "python3", "ls", "cat", "head", "tail", "wc", "grep", "find", "stat")
_SHELL_OPERATORS = {"|", "&&", "||", ";", ">", ">>", "<", "2>", "&"}
#: python/node short options that run inline code / a REPL (``-c CODE``, ``-e CODE``, ``-p``, ``-i``)
#: or load code by path we cannot vet (``-r``); combined forms (``-Bc``, ``-pe``) are caught per letter.
_INLINE_SHORT = {"python": set("cim"), "python3": set("cim"), "node": set("epir")}
_INLINE_LONG = ("--eval", "--print", "--interactive", "--input-type", "--require", "--import", "--loader",
                "--experimental-loader", "--experimental-default-type")
#: ``python -m <module>`` is allowed only for these stdlib modules (syntax / JSON checks)
PY_MODULE_ALLOW = ("py_compile", "compileall", "json.tool", "ast", "tokenize")
_FIND_DENY = ("-exec", "-execdir", "-ok", "-okdir", "-delete", "-fprint", "-fprintf", "-fls", "-fprint0")
_SKIP_GLOB_DIRS = (".git", "node_modules", "__pycache__", "trajectories")


@dataclass
class ToolOutcome:
    text: str
    images: list[ImagePart] = field(default_factory=list)
    is_error: bool = False
    numbers: dict[str, Any] = field(default_factory=dict)


class PathDenied(ValueError):
    pass


def _schema(props: dict[str, dict[str, Any]], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required}


class FileTools:
    """read_file / write_file / edit_file / list_files / run_shell inside one workspace."""

    def __init__(self, ws: Workspace, write_roots: list[str], *, allow_shell: bool = True):
        self.ws = ws
        self.write_roots = [(ws.root / r).resolve() for r in write_roots]
        self.allow_shell = allow_shell
        self.writes: list[str] = []  # workspace-relative paths written/edited, in order

    # ------------------------------------------------------------------ specs
    def specs(self) -> list[ToolSpec]:
        roots = ", ".join(os.path.relpath(r, self.ws.root) + "/" for r in self.write_roots)
        specs = [
            ToolSpec(name="read_file", description="Read a text file in the workspace (path relative to the workspace root).",
                     parameters=_schema({"path": {"type": "string", "description": "workspace-relative path"}}, ["path"])),
            ToolSpec(name="write_file", description=f"Create or overwrite a text file. Only allowed under: {roots}. Parent dirs are created.",
                     parameters=_schema({"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"])),
            ToolSpec(name="edit_file", description=f"Exact-string replacement in an existing file (under {roots}). `old` must occur exactly once unless all=true.",
                     parameters=_schema({"path": {"type": "string"}, "old": {"type": "string"}, "new": {"type": "string"},
                                         "all": {"type": "boolean", "description": "replace every occurrence", "default": False}},
                                        ["path", "old", "new"])),
            ToolSpec(name="list_files", description="List workspace files matching a glob (default '**/*' under src/). Hidden/git/node_modules skipped.",
                     parameters=_schema({"glob": {"type": "string", "description": "e.g. 'src/**/*.js'", "default": "src/**/*"}}, [])),
        ]
        if self.allow_shell:
            specs.append(ToolSpec(
                name="run_shell",
                description=f"Run ONE command in the workspace root (no pipes/redirects/network). Allowed programs: {', '.join(SHELL_ALLOW)}. "
                            "Use it to syntax-check (`node --check src/x.js`, `python -m py_compile src/model.py`) or inspect files. "
                            "Inline code (`python -c`, `node -e`) and paths outside the workspace are rejected.",
                parameters=_schema({"cmd": {"type": "string", "description": "the command line, e.g. 'node --check src/object.js'"},
                                    "timeout_s": {"type": "integer", "default": 60}}, ["cmd"]),
            ))
        return specs

    def names(self) -> set[str]:
        return {s.name for s in self.specs()}

    # ------------------------------------------------------------------ dispatch
    def call(self, name: str, args: dict[str, Any]) -> ToolOutcome:
        fn = {"read_file": self.read_file, "write_file": self.write_file, "edit_file": self.edit_file,
              "list_files": self.list_files, "run_shell": self.run_shell}.get(name)
        if fn is None or (name == "run_shell" and not self.allow_shell):
            return ToolOutcome(f"unknown tool {name!r}", is_error=True)
        try:
            return fn(**args)
        except TypeError as e:
            return ToolOutcome(f"{name}: bad arguments: {e}", is_error=True)
        except PathDenied as e:
            return ToolOutcome(f"{name}: {e}", is_error=True)
        except (OSError, ValueError) as e:  # ValueError covers UnicodeDecodeError / bad numbers
            return ToolOutcome(f"{name}: {type(e).__name__}: {e}", is_error=True)
        except Exception as e:  # noqa: BLE001 — tools never raise into the agent loop
            return ToolOutcome(f"{name}: {type(e).__name__}: {e}", is_error=True)

    # ------------------------------------------------------------------ paths
    def _resolve(self, path: str, *, write: bool) -> Path:
        if not isinstance(path, str) or not path.strip():
            raise PathDenied("path must be a non-empty workspace-relative string")
        p = Path(path)
        p = (self.ws.root / p).resolve() if not p.is_absolute() else p.resolve()
        root = self.ws.root.resolve()
        if p != root and root not in p.parents:
            raise PathDenied(f"path escapes the workspace: {path}")
        if ".git" in p.relative_to(root).parts:
            raise PathDenied("the .git directory is off limits")
        if write and not any(p == r or r in p.parents for r in self.write_roots):
            roots = ", ".join(os.path.relpath(r, root) + "/" for r in self.write_roots)
            raise PathDenied(f"writes are only allowed under {roots} (got {path})")
        return p

    # ------------------------------------------------------------------ tools
    def read_file(self, path: str) -> ToolOutcome:
        p = self._resolve(path, write=False)
        if not p.is_file():
            return ToolOutcome(f"no such file: {path}", is_error=True)
        text = p.read_text(errors="replace")
        if len(text) > READ_CAP_CHARS:
            text = text[:READ_CAP_CHARS] + f"\n... [truncated; file has {len(text)} chars]"
        return ToolOutcome(text)

    def write_file(self, path: str, content: str) -> ToolOutcome:
        p = self._resolve(path, write=True)
        p.parent.mkdir(parents=True, exist_ok=True)
        existed = p.exists()
        p.write_text(content)
        rel = os.path.relpath(p, self.ws.root)
        self.writes.append(rel)
        return ToolOutcome(f"{'overwrote' if existed else 'created'} {rel} ({len(content)} chars, {content.count(chr(10)) + 1} lines)")

    def edit_file(self, path: str, old: str, new: str, all: bool = False) -> ToolOutcome:  # noqa: A002 - API name
        p = self._resolve(path, write=True)
        if not p.is_file():
            return ToolOutcome(f"no such file: {path} (use write_file to create it)", is_error=True)
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return ToolOutcome(f"{path} is not UTF-8 text; edit_file only works on text files (use write_file to replace it)",
                               is_error=True)
        n = text.count(old)
        if not old:
            return ToolOutcome("`old` must be non-empty; read the file and quote the exact text to replace", is_error=True)
        if n == 0:
            return ToolOutcome(f"`old` not found in {path}; read_file it and copy the exact text", is_error=True)
        if n > 1 and not all:
            return ToolOutcome(f"`old` occurs {n} times in {path}; include more context or pass all=true", is_error=True)
        p.write_text(text.replace(old, new) if all else text.replace(old, new, 1))
        rel = os.path.relpath(p, self.ws.root)
        self.writes.append(rel)
        return ToolOutcome(f"edited {rel}: replaced {n if all else 1} occurrence(s)")

    def list_files(self, glob: str = "src/**/*") -> ToolOutcome:
        root = self.ws.root
        out = []
        for p in sorted(root.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(root).as_posix()
            if any(part in _SKIP_GLOB_DIRS or part.startswith(".") for part in Path(rel).parts[:-1]):
                continue
            if fnmatch.fnmatch(rel, glob) or fnmatch.fnmatch(rel, glob.replace("**/", "")):
                out.append(f"{rel}  ({p.stat().st_size} B)")
            if len(out) >= 300:
                out.append("... (truncated at 300)")
                break
        return ToolOutcome("\n".join(out) if out else f"no files match {glob!r}")

    # ------------------------------------------------------------------ shell policy
    def _arg_escapes(self, a: str) -> bool:
        """True when ``a`` (a path-like argument) resolves outside the workspace or into .git."""
        root = self.ws.root.resolve()
        cand = Path(os.path.realpath(os.path.join(root, os.path.expanduser(a))))
        if cand != root and root not in cand.parents:
            return True
        return cand != root and ".git" in cand.relative_to(root).parts

    def _shell_policy_error(self, argv: list[str]) -> str | None:
        """Why ``argv`` is refused (None = allowed).  See the module docstring."""
        if not argv or os.path.basename(argv[0]) not in SHELL_ALLOW:
            return f"program {argv[0] if argv else ''!r} not allowed; use one of {SHELL_ALLOW}"
        prog = os.path.basename(argv[0])
        inline = _INLINE_SHORT.get(prog, set())
        rest = argv[1:]
        for i, a in enumerate(rest):
            if a == "-" or (prog == "find" and a in _FIND_DENY):
                return f"{a!r} is not allowed (stdin scripts / find actions are disabled)"
            if a.startswith("--"):
                opt, _, val = a.partition("=")
                if opt in _INLINE_LONG:
                    return f"{opt} is not allowed: inline code / custom loaders are disabled; put code in a file under src/"
                if val and self._arg_escapes(val):
                    return f"paths outside the workspace are not allowed: {val}"
                continue
            if a.startswith("-") and len(a) > 1:
                letters = a[1:]
                if prog.startswith("python") and "m" in letters:
                    pre, _, module = letters.partition("m")  # -Bm mod / -mmod; -cm is `-c "m"`
                    if set(pre) & inline:
                        return f"{a} is not allowed: inline code / REPL flags are disabled; put code in a file under src/"
                    module = module or (rest[i + 1] if i + 1 < len(rest) else "")
                    if module not in PY_MODULE_ALLOW:
                        return f"python -m {module!r} is not allowed; allowed modules: {PY_MODULE_ALLOW}"
                    continue
                if set(letters) & inline:
                    return f"{a} is not allowed: inline code / REPL flags are disabled; put code in a file under src/"
                continue
            if self._arg_escapes(a):
                return f"paths outside the workspace are not allowed: {a}"
        return None

    def run_shell(self, cmd: str, timeout_s: int | float | str = 60) -> ToolOutcome:
        try:
            argv = shlex.split(cmd)
        except ValueError as e:
            return ToolOutcome(f"cannot parse command: {e}", is_error=True)
        if any(tok in _SHELL_OPERATORS for tok in argv):  # no shell is involved; this is just a clear message
            return ToolOutcome("shell operators (| && ; > <) are not allowed; run ONE plain command", is_error=True)
        why = self._shell_policy_error(argv)
        if why:
            return ToolOutcome(why, is_error=True)
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.ws.root), "LANG": "C.UTF-8",
            "NODE_PATH": str(get_settings().runtime_js_dir() / "node_modules"), "PYTHONDONTWRITEBYTECODE": "1",
            "NO_PROXY": "*", "no_proxy": "*",
        }
        t = float(max(5, min(_as_int(timeout_s, 60), int(SHELL_TIMEOUT_S))))
        proc = run_with_watchdog(argv, cwd=self.ws.root, env=env, soft_timeout_s=t, idle_grace_s=5, hard_timeout_s=t + 5,
                                 activity_dirs=[], poll_s=0.2)
        body = f"$ {cmd}\nrc={proc.rc}" + (" (killed: timeout)" if proc.timed_out else "")
        if proc.stdout.strip():
            body += "\n--- stdout ---\n" + _cap(proc.stdout)
        if proc.stderr.strip():
            body += "\n--- stderr ---\n" + _cap(proc.stderr)
        return ToolOutcome(body, is_error=proc.rc != 0)


def _as_int(value: Any, default: int) -> int:
    """Lenient int coercion for model-supplied numbers ('60', 60.5, 'soon' → default)."""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _cap(text: str, n: int = SHELL_OUT_CAP) -> str:
    if len(text) <= n:
        return text
    head, tail_ = n // 4, n - n // 4
    return text[:head] + f"\n... [{len(text) - n} chars omitted] ...\n" + text[-tail_:]


class SpatialTools:
    """Bridge from the ``codeverse.spatial.registry`` to native tool calls."""

    def __init__(self, ws: Workspace, *, track: str = "", language: str = "", round_index: int = 0):
        from codeverse.spatial.registry import ToolContext, list_tools

        self.ctx = ToolContext(workspace=ws, round_index=round_index, language=language, track=track)
        self.tools = {t.name: t for t in list_tools(track=track, language=language)}

    def specs(self) -> list[ToolSpec]:
        return [ToolSpec(name=t.name, description=t.description, parameters=t.schema()) for t in self.tools.values()]

    def names(self) -> set[str]:
        return set(self.tools)

    def call(self, name: str, args: dict[str, Any]) -> ToolOutcome:
        t = self.tools.get(name)
        if t is None:
            return ToolOutcome(f"unknown spatial tool {name!r}", is_error=True)
        obs = t.call(self.ctx, args)
        text = obs.text
        if obs.numbers:
            text += "\n\nnumbers: " + json.dumps(obs.numbers, default=str)[:4000]
        images = [ImagePart(path=p, label=Path(p).stem) for p in obs.images if Path(p).is_file()]
        return ToolOutcome(text, images=images, is_error=not obs.ok, numbers=obs.numbers)


__all__ = ["FileTools", "SpatialTools", "ToolOutcome", "PathDenied", "SHELL_ALLOW", "PY_MODULE_ALLOW"]
