"""Task 1: per track x corpus, median / p90 of run wall and every stage, plus
"waiting on the provider" (retry sleeps + failed attempts), derived from transcript
timestamps (agent sessions) and from judge/planner stage span minus recorded latency.
Writes stage_runs.jsonl (one row per run) and prints the table."""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
from corpus import RunRef, cost_rows, discover, fmt, med_p90, rounds, trajectories  # noqa: E402
from turns import parse_turns, split  # noqa: E402

HERE = Path(__file__).parent


def round_segments(ev: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One dict per round.start..round.done segment (an abandoned segment ends at the
    next round.start / run.start and is flagged)."""
    segs: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    cand: dict[Any, float] = {}  # best-of-N candidates are generated BEFORE round.start
    for e in ev:
        k = e["event"]
        if k == "candidates.start":
            cand[e.get("round")] = e["t"]
        if k in ("round.start", "run.start"):
            if cur is not None:
                cur["abandoned"] = True
                cur["end"] = e["t"]
                segs.append(cur)
            cur = None
            if k == "round.start":
                cur = {"round": e.get("round"), "start": min(e["t"], cand.pop(e.get("round"), e["t"])), "abandoned": False}
            continue
        if cur is None:
            continue
        if k == "build.done":
            cur.setdefault("first_build", e["t"])
            cur["last_build"] = e["t"]
            cur["build_ms"] = cur.get("build_ms", 0) + int(e.get("duration_ms") or 0)
            cur["n_builds"] = cur.get("n_builds", 0) + 1
        elif k == "gates.done":
            cur["gates_t"] = e["t"]
        elif k == "judge.done":
            cur["judge_s"] = float(e.get("duration_s") or 0)
            cur["judge_t"] = e["t"]
        elif k == "round.done":
            cur["end"] = e["t"]
            cur["duration_s"] = float(e.get("duration_s") or (e["t"] - cur["start"]))
            segs.append(cur)
            cur = None
    if cur is not None:
        cur["abandoned"] = True
        cur["end"] = ev[-1]["t"]
        segs.append(cur)
    return segs


def analyse(ref: RunRef) -> dict[str, Any]:
    ev = ref.events
    done = [e for e in ev if e["event"] == "run.done"]
    row: dict[str, Any] = {
        "corpus": ref.corpus, "bucket": ref.bucket, "track": ref.track, "run": ref.name,
        "finished": bool(done), "wall_s": ev[-1]["t"] - ev[0]["t"],
        "status": done[-1].get("status") if done else "RUNNING",
    }
    plan_s = sum(float(e.get("duration_s") or 0) for e in ev if e["event"] == "stage.done" and e.get("stage") == "plan")
    row["plan_s"] = plan_s
    scene_gen = sum(float(e.get("duration_s") or 0) for e in ev
                    if e["event"] == "stage.done" and e.get("stage") in ("assets", "env", "zones", "assemble"))
    row["plan_cached"] = any(e["event"] == "stage.cached" and e.get("stage") == "plan" for e in ev)
    rows = cost_rows(ref)
    lat = lambda role, ok=True: sum(  # noqa: E731
        r["latency_ms"] / 1000 for r in rows if r["role"] == role and (r["outcome"] == "ok") == ok)
    row["plan_model_s"] = lat("planner")
    row["plan_wait_s"] = max(0.0, plan_s - lat("planner")) if plan_s else 0.0
    row["judge_err_s"] = lat("judge", ok=False)  # error rows carry the whole failed span
    segs = [s for s in round_segments(ev) if not s["abandoned"]]
    row["abandoned_s"] = sum(s["end"] - s["start"] for s in round_segments(ev) if s["abandoned"])
    rd = {r.get("index"): r for r in rounds(ref)}
    gen, build, gates, render, judge, other = [], [], [], [], [], []
    for s in segs:
        if "gates_t" not in s or "last_build" not in s:
            continue
        b = s["build_ms"] / 1000
        gen.append(s["last_build"] - s["start"] - b)
        build.append(b)
        gates.append(s["gates_t"] - s["last_build"])
        r = rd.get(s["round"], {})
        rnd = float(((r.get("renders") or {}).get("duration_ms") or 0)) / 1000
        render.append(rnd)
        j = s.get("judge_s", 0.0)
        judge.append(j)
        if "judge_t" in s:
            other.append(max(0.0, s["judge_t"] - s["gates_t"] - j - rnd))
    judge_ok = lat("judge")
    row.update(gen_s=sum(gen) + scene_gen, build_s=sum(build), gates_s=sum(gates), render_s=sum(render),
               judge_s=sum(judge), other_s=sum(other), n_rounds=len(gen),
               judge_model_s=judge_ok, judge_wait_s=max(0.0, sum(judge) - judge_ok))
    think = tools = wait = sess = 0.0
    n_sess = 0
    for _, res, tr in trajectories(ref):
        a, b2, c = split(parse_turns(tr))
        think, tools, wait, sess, n_sess = think + a, tools + b2, wait + c, sess + float(res.get("duration_s") or 0), n_sess + 1
    row.update(sess_think_s=think, sess_tool_s=tools, sess_wait_s=wait, sess_total_s=sess, n_sessions=n_sess)
    row["wait_total_s"] = wait + row["plan_wait_s"] + row["judge_wait_s"]
    return row


def main() -> None:
    refs = discover()
    out = [analyse(r) for r in refs]
    (HERE / "stage_runs.jsonl").write_text("".join(json.dumps(r) + "\n" for r in out))
    cols = ["wall_s", "plan_s", "gen_s", "build_s", "gates_s", "render_s", "judge_s", "wait_total_s", "sess_wait_s", "judge_wait_s", "judge_err_s", "abandoned_s"]
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in out:
        groups[(r["bucket"], r["track"])].append(r)
    print("| corpus | track | n(fin) | " + " | ".join(f"{c} med/p90" for c in cols) + " |")
    print("|" + "---|" * (len(cols) + 3))
    for (bucket, track), rs in sorted(groups.items()):
        fin = [r for r in rs if r["finished"]]
        cells = []
        for c in cols:
            src = fin if c == "wall_s" else rs
            m, p, _ = med_p90([float(r[c]) for r in src])
            cells.append(f"{fmt(m)}/{fmt(p)}")
        print(f"| {bucket} | {track} | {len(rs)}({len(fin)}) | " + " | ".join(cells) + " |")
    print("\nshare of run wall: waiting / thinking / tools (medians of per-run ratios, finished runs)")
    for (bucket, track), rs in sorted(groups.items()):
        fin = [r for r in rs if r["finished"] and r["wall_s"] > 0]
        if not fin:
            continue
        w = med_p90([100 * r["wait_total_s"] / r["wall_s"] for r in fin])[0]
        th = med_p90([100 * r["sess_think_s"] / r["wall_s"] for r in fin])[0]
        to = med_p90([100 * (r["sess_tool_s"] + r["build_s"] + r["gates_s"] + r["render_s"]) / r["wall_s"] for r in fin])[0]
        jm = med_p90([100 * r["judge_model_s"] / r["wall_s"] for r in fin])[0]
        print(f"  {bucket:14s} {track:16s} wait {fmt(w)}%  think {fmt(th)}%  tools+build+gates+render {fmt(to)}%  judge model {fmt(jm)}%")
    # per-round view (generate + judge per round) for the storm/baseline contrast
    print("\nper-round: gen_s and judge_s med/p90 (rounds with a build+gates)")
    per: dict[tuple[str, str], tuple[list[float], list[float]]] = defaultdict(lambda: ([], []))
    for r in out:
        n = max(1, r["n_rounds"])
        per[(r["bucket"], r["track"])][0].append(r["gen_s"] / n)
        per[(r["bucket"], r["track"])][1].append(r["judge_s"] / n)
    for k, (g, j) in sorted(per.items()):
        gm, gp, _ = med_p90(g)
        jm, jp, _ = med_p90(j)
        print(f"  {k[0]:14s} {k[1]:16s} gen {fmt(gm)}/{fmt(gp)}  judge {fmt(jm)}/{fmt(jp)}")


if __name__ == "__main__":
    main()
