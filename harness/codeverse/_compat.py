"""Standard-library shims for the supported python floor (**3.10**).

The harness is developed on 3.13 but must install and run on every interpreter
in ``requires-python`` (see ``docs/INSTALL.md`` §2.1 "Supported versions").  This
module is the *only* place that bridges that gap: import the name from here and
write ordinary code everywhere else — no ``sys.version_info`` branches scattered
through the package.

Every shim is defined **unconditionally**, i.e. 3.10 and 3.13 execute the same
objects, so what CI proves on the floor is what runs on the newest interpreter.
The one exception is :data:`tomllib`, which cannot be emulated and falls back to
the ``tomli`` backport (a conditional dependency in ``pyproject.toml``).

| shim | replaces | added to the stdlib in | delete this shim when the floor is |
|---|---|---|---|
| :class:`StrEnum` | ``enum.StrEnum`` | 3.11 | ≥ 3.11 |
| :data:`UTC`      | ``datetime.UTC`` | 3.11 | ≥ 3.11 |
| :data:`tomllib`  | ``tomllib``      | 3.11 | ≥ 3.11 |

Deleting one is mechanical: raise the floor in ``pyproject.toml``, then
``from enum import StrEnum`` / ``from datetime import UTC`` / ``import tomllib``
at the call sites and drop the entry here.
"""

from __future__ import annotations

from datetime import timezone
from enum import Enum

__all__ = ["UTC", "StrEnum", "tomllib"]


#: ``datetime.UTC``, the 3.11 spelling of the singleton it aliases.
UTC = timezone.utc


class StrEnum(str, Enum):
    """``enum.StrEnum`` (3.11+) for the 3.10 floor.

    Same observable behaviour as the stdlib class for the way the harness uses
    it — ``str(x)``/``format(x)``/``"%s"``/``json.dumps`` all give the *value*,
    ``repr`` stays ``<Cls.NAME: 'value'>``, members are real ``str`` (so they
    compare, hash, sort and serialise as their value), and ``auto()`` yields the
    lower-cased member name.  Unlike the stdlib class it does not reject
    non-``str`` member values; every enum here spells its values out as string
    literals, and mypy/ruff would catch a stray one.
    """

    # Enum's own __str__/__format__ would print "Cls.NAME"; the stdlib StrEnum
    # inherits str's instead (via ReprEnum).  Bind them explicitly so the
    # behaviour is the same on 3.10 (mixin rules) and on 3.11+ (ReprEnum rules).
    __str__ = str.__str__
    __format__ = str.__format__

    @staticmethod
    def _generate_next_value_(name: str, start: int, count: int, last_values: list) -> str:
        return name.lower()


try:  # 3.11+
    import tomllib
except ModuleNotFoundError:  # 3.10 — pyproject pulls the `tomli` backport there
    import tomli as tomllib  # type: ignore[no-redef]
