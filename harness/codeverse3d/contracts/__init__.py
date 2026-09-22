"""Typed contracts shared across the harness.  Data + validators — no I/O, no model calls.

Modules:
- ``common``    enums, vectors, usage/cost.
- ``spec``      what the user asked for (track, language, prompt, budget, backends).
- ``plan``      what the planner decided (parts / joints / zones / acceptance).
- ``artifacts`` what tools produced (measurements, renders, gate reports, builds) and
                what judges said (scores, issues, improvement plan).
- ``run``       the run record (rounds, totals, provenance) — the flywheel unit.
- ``chat``      ChatModel request/response shapes.
- ``agent``     CodingAgent job/result shapes.

Every name is imported from its module; the package itself re-exports nothing.
"""
