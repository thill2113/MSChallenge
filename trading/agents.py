"""Multi-agent orchestration.

Design rules:
- One ExchangeClient instance per agent. Clients are never shared across
  agents, so sessions, rate limiters, and auth state stay isolated and no
  cross-thread locking is needed above the limiter itself.
- Agents run in their own threads (I/O-bound workloads; swap in asyncio or
  processes if strategy compute grows). The orchestrator only starts/stops
  them and collects results via a thread-safe queue.
- A strategy is any callable that receives the agent's client and returns
  when done; the agent loop handles errors and restart/backoff.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable

from .base import ExchangeClient

log = logging.getLogger(__name__)

Strategy = Callable[[ExchangeClient, "AgentContext"], None]


@dataclass
class AgentContext:
    """Handles an agent can use to publish results and check for shutdown."""
    agent_id: str
    results: queue.Queue
    stop_event: threading.Event

    def should_stop(self) -> bool:
        return self.stop_event.is_set()

    def publish(self, item) -> None:
        self.results.put((self.agent_id, item))


class TradingAgent(threading.Thread):
    """Wraps one client + one strategy in a supervised loop."""

    def __init__(self, agent_id: str, client_factory: Callable[[], ExchangeClient],
                 strategy: Strategy, results: queue.Queue,
                 max_restarts: int = 3):
        super().__init__(name=agent_id, daemon=True)
        self.agent_id = agent_id
        self._client_factory = client_factory
        self._strategy = strategy
        self._results = results
        self._max_restarts = max_restarts
        self.stop_event = threading.Event()

    def run(self) -> None:
        ctx = AgentContext(self.agent_id, self._results, self.stop_event)
        restarts = 0
        while not self.stop_event.is_set():
            try:
                # fresh client per (re)start: clean session + limiter state
                with self._client_factory() as client:
                    self._strategy(client, ctx)
                return  # strategy completed normally
            except Exception:
                restarts += 1
                log.exception("[%s] strategy crashed (restart %d/%d)",
                              self.agent_id, restarts, self._max_restarts)
                if restarts > self._max_restarts:
                    ctx.publish({"error": "max restarts exceeded"})
                    return
                # exponential backoff before restart
                self.stop_event.wait(min(2 ** restarts, 60))

    def stop(self) -> None:
        self.stop_event.set()


class Orchestrator:
    """Starts agents, fans in their results, and shuts them down."""

    def __init__(self):
        self.results: queue.Queue = queue.Queue()
        self._agents: list[TradingAgent] = []

    def add_agent(self, agent_id: str,
                  client_factory: Callable[[], ExchangeClient],
                  strategy: Strategy) -> TradingAgent:
        agent = TradingAgent(agent_id, client_factory, strategy, self.results)
        self._agents.append(agent)
        return agent

    def start(self) -> None:
        for agent in self._agents:
            agent.start()

    def stop(self, timeout: float = 30.0) -> None:
        for agent in self._agents:
            agent.stop()
        deadline = time.monotonic() + timeout
        for agent in self._agents:
            agent.join(max(0.0, deadline - time.monotonic()))

    def drain_results(self) -> list[tuple[str, object]]:
        items = []
        while True:
            try:
                items.append(self.results.get_nowait())
            except queue.Empty:
                return items
