"""Shared parity-test helpers.

One source of truth for what "signature parity" means across the per-client
parity suites and the consolidated full-surface gate. Previously each parity
module defined its own verbatim copy of ``_param_signature``; hoisting it here
keeps a single definition so the suites can never disagree on the comparison.
"""

from __future__ import annotations

import inspect


def _param_signature(cls: type, name: str) -> list[tuple[str, inspect._ParameterKind]]:
    """The (name, kind) of each parameter of ``cls.name`` except ``self``.

    Names + kinds are what make a call expression bind identically across the
    two clients; annotations/defaults are deliberately excluded (our return
    models differ from py-sdk's by design, and defaults aren't part of the
    call-site contract a port depends on).
    """
    sig = inspect.signature(getattr(cls, name))
    return [(pname, p.kind) for pname, p in sig.parameters.items() if pname != "self"]
