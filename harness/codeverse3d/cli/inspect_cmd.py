"""``3dcodeverse judge`` — re-judge a round of one existing run into ``artifacts/judge/rNN_cli.json``."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from codeverse3d.cli import _common as C
from codeverse3d.cli._common import RunsDirOpt, console, kv_table
from codeverse3d.config import get_settings


def judge(
    slug: str,
    round_index: Annotated[int | None, typer.Option("--round")] = None,
    rubric: Annotated[
        str | None, typer.Option("--rubric", help="default: the rubric the round was judged with")
    ] = None,
    model: Annotated[str | None, typer.Option("--model", help="judge chat model id")] = None,
    n: Annotated[int | None, typer.Option("--n", min=1, help="samples (default: Settings.judge.samples)")] = None,
    thinking: Annotated[
        str | None, typer.Option("--thinking", help="off | low | medium | high (default: Settings.judge.thinking, "
                                                    "C3D_JUDGE__THINKING; gemini-4-argon thinks only from medium)"),
    ] = None,
    runs_dir: RunsDirOpt = None,
) -> None:
    """Re-judge a round with the same inputs as the in-run judge; writes artifacts/judge/rNN_cli.json."""
    from codeverse3d.addons import select
    from codeverse3d.cli import _judge as J
    from codeverse3d.record.record import RecordError, join_post_run, load_record

    ws = C.open_workspace(slug, runs_dir)
    try:
        rec = load_record(ws)
    except RecordError as e:
        raise C.CliError(str(e), code=2) from e
    idx = round_index if round_index is not None else select.summarise(ws.root, record=rec).round
    if idx is None:
        raise C.CliError(f"no round of {ws.root.name} built with renders: nothing to judge (--round N names one)")
    rnd = J.load_round(ws, rec, idx)
    if rnd is None or rnd.renders is None or not rnd.renders.views:
        raise C.CliError(f"round {idx} has no renders (rounds/r{idx:02d}.json / record.json)")
    rubric_name = J.rubric_for(rec, rnd, rubric)
    with C.replay_variant(rec):
        inp = J.build_judge_input(ws, rec, rnd)
    missing = [v.path for v in inp.renders.views if not Path(v.path).is_file()]
    if missing:
        raise C.CliError(f"round {idx}: {len(missing)} of its {len(inp.renders.views)} judged renders are not on disk "
                         f"(e.g. {missing[0]}): nothing to re-judge it from", code=2)
    try:
        judge_obj = J.make_judge(rec.spec, rubric_name, model or rec.spec.backends.judge,
                                 n or get_settings().judge.samples,
                                 **({"thinking": thinking} if thinking else {}))
    except ValueError as e:
        raise C.CliError(str(e), code=2) from e
    # writes into the run: one writer per run dir; judged under the run's variant
    with C.mutating(ws, what=f"3dcodeverse judge {ws.root.name}", action="judge"), C.replay_variant(rec):
        try:
            with join_post_run(ws):
                verdict = judge_obj.judge(inp)
        except ValueError as e:
            raise C.CliError(f"judge failed: {e}") from e
        except Exception as e:
            from codeverse3d.judges.vlm_judge import ReferenceJudgeError

            if isinstance(e, ReferenceJudgeError):
                raise C.CliError(f"judge failed: {e}") from e
            raise
        ws.write_json(ws.judge_path(idx, "_cli"), verdict)
    console.print(
        kv_table(
            "judgment",
            {
                "rubric": verdict.rubric,
                "round": idx,
                "overall": f"{verdict.overall:.3f}",
                "passed": verdict.passed,
                "std": verdict.score_std,
                "thinking": getattr(judge_obj, "thinking", "-"),
                "stored score": f"{rnd.judgment.overall:.3f}" if rnd.judgment else "-",
                "summary": verdict.summary,
                "cost": f"${verdict.usage.cost_usd:.4f}",
            },
        )
    )
    for it in verdict.improvement_plan:
        console.print(f"  [{it.priority}] {it.target}: {it.instruction}")
