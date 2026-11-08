"""Typed contracts shared by every instrument.  Data + validators — no I/O.

Modules:
- ``common``    enums (track, language), the entry files, joint-coupling rules.
- ``spec``      what the project asks for (``spec.json``).
- ``plan``      the agent's plan (parts / joints / zones / passes / acceptance), validated in code.
- ``artifacts`` what instruments produce (builds, measurements, renders, gate reports) and the judgment shape.

Every name is imported from its module; the package itself re-exports nothing.
"""
