"""Version-honesty tests.

``polysim_sdk.__version__`` and the default ``User-Agent`` must track the
package's installed version (sourced once from package metadata), not a stale
hardcoded literal.
"""

from __future__ import annotations

import importlib.metadata


def test_sdk_version_matches_package_metadata() -> None:
    import polysim_sdk

    assert polysim_sdk.__version__ == importlib.metadata.version("polysimulator")


def test_default_user_agent_carries_the_version() -> None:
    from polysim_sdk._version import DEFAULT_USER_AGENT, __version__

    assert DEFAULT_USER_AGENT == f"polysim-sdk/{__version__}"
    # No stale literal hiding in the UA.
    assert "0.2.2" not in DEFAULT_USER_AGENT


def test_http_transport_default_user_agent_is_dynamic() -> None:
    """The sync transport's default UA must be the dynamic version, not 0.2.2."""
    from polysim_sdk._version import DEFAULT_USER_AGENT
    from polysim_sdk.client import PolySimClient

    client = PolySimClient(api_key="ps_live_x", base_url="https://api.polysimulator.test")
    try:
        ua = client._transport._http.headers.get("user-agent")
        assert ua == DEFAULT_USER_AGENT
        assert "0.2.2" not in (ua or "")
    finally:
        client.close()
