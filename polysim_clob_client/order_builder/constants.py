"""py-clob-client-compatible order-side constants.

``from py_clob_client.order_builder.constants import BUY, SELL`` ports to
``from polysim_clob_client.order_builder.constants import BUY, SELL`` verbatim.
"""

from __future__ import annotations

BUY = "BUY"
SELL = "SELL"

__all__ = ["BUY", "SELL"]
