"""WebSocket market-data streams for Coinbase Advanced Trade and Binance.US.

Robinhood has no public WebSocket feed — poll its REST quotes instead
(see RobinhoodClient's rate limiter).

Both streams follow the same pattern: a daemon thread owns the socket,
reconnects with exponential backoff + jitter, and hands parsed messages to a
callback. Market data flows over WebSocket precisely so the REST budget
stays reserved for order placement.
"""

from __future__ import annotations

import json
import logging
import random
import threading
import time
from typing import Callable

from websocket import WebSocketApp  # websocket-client

log = logging.getLogger(__name__)

OnMessage = Callable[[dict], None]


class _ReconnectingStream(threading.Thread):
    url: str

    def __init__(self, on_message: OnMessage):
        super().__init__(daemon=True)
        self._on_message = on_message
        self._stop = threading.Event()
        self._ws: WebSocketApp | None = None

    def _subscribe(self, ws: WebSocketApp) -> None:
        """Override to send subscription messages after connect."""

    def run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            self._ws = WebSocketApp(
                self.url,
                on_open=lambda ws: (self._subscribe(ws),),
                on_message=lambda ws, msg: self._on_message(json.loads(msg)),
                on_error=lambda ws, err: log.warning("%s: %s", self.url, err),
            )
            self._ws.run_forever(ping_interval=20, ping_timeout=10)
            if self._stop.is_set():
                return
            # jittered exponential backoff avoids thundering-herd reconnects
            sleep = backoff + random.uniform(0, backoff / 2)
            log.info("stream disconnected; reconnecting in %.1fs", sleep)
            self._stop.wait(sleep)
            backoff = min(backoff * 2, 60.0)

    def stop(self) -> None:
        self._stop.set()
        if self._ws:
            self._ws.close()


class CoinbaseTickerStream(_ReconnectingStream):
    """Coinbase Advanced Trade WS. Public channels need no auth; connection
    limit is per-IP, and one socket can multiplex many products."""

    url = "wss://advanced-trade-ws.coinbase.com"

    def __init__(self, product_ids: list[str], on_message: OnMessage):
        super().__init__(on_message)
        self._product_ids = product_ids

    def _subscribe(self, ws: WebSocketApp) -> None:
        ws.send(json.dumps({
            "type": "subscribe",
            "channel": "ticker",
            "product_ids": self._product_ids,
        }))


class BinanceUSTickerStream(_ReconnectingStream):
    """Binance.US combined stream. Streams are named in the URL, so no
    subscribe message is needed. Binance force-closes sockets after 24h —
    the reconnect loop handles that as a normal disconnect."""

    def __init__(self, symbols: list[str], on_message: OnMessage):
        streams = "/".join(f"{s.lower()}@ticker" for s in symbols)
        self.url = f"wss://stream.binance.us:9443/stream?streams={streams}"
        super().__init__(on_message)
