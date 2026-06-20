"""Single source of truth for the SDK version + default User-Agent.

The version is read once from the installed package metadata
(``importlib.metadata.version("polysimulator")``) so ``polysim_sdk.__version__``
and the outbound ``User-Agent`` can never drift from ``[project].version`` in
``pyproject.toml``. A literal fallback covers an editable / not-yet-installed
checkout where the distribution metadata is absent.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

# Fallback for a checkout with no installed distribution metadata (e.g. running
# straight from source without ``pip install -e``). Kept in sync with
# ``[project].version`` as a last resort only — the metadata read is canonical.
_FALLBACK_VERSION = "0.4.2"

try:
    __version__ = version("polysimulator")
except PackageNotFoundError:  # pragma: no cover - exercised only in bare checkouts
    __version__ = _FALLBACK_VERSION

# The default outbound User-Agent. Built once from the resolved version so every
# transport (sync/async HTTP + SSE + WS) sends a UA that matches the package.
DEFAULT_USER_AGENT = f"polysim-sdk/{__version__}"

__all__ = ["DEFAULT_USER_AGENT", "__version__"]
