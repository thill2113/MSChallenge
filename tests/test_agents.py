from decimal import Decimal

from trading.agents import Orchestrator
from trading.base import Balance, ExchangeClient


class FakeClient(ExchangeClient):
    name = "fake"

    def get_balances(self):
        return [Balance(asset="BTC", free=Decimal("1"))]

    def place_limit_order(self, *a, **k):
        raise NotImplementedError

    def close(self):
        pass


def test_orchestrator_runs_isolated_agents_and_collects_results():
    orch = Orchestrator()
    seen = []

    def strategy(client, ctx):
        seen.append(client)  # hold a strong reference so ids can't be reused
        ctx.publish(client.get_balances())

    orch.add_agent("a1", FakeClient, strategy)
    orch.add_agent("a2", FakeClient, strategy)
    orch.start()
    orch.stop(timeout=10)

    results = orch.drain_results()
    assert sorted(agent_id for agent_id, _ in results) == ["a1", "a2"]
    # each agent got its own client instance
    assert len(seen) == 2 and seen[0] is not seen[1]


def test_agent_restarts_after_crash_then_completes():
    orch = Orchestrator()
    attempts = []

    def flaky(client, ctx):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("boom")
        ctx.publish("recovered")

    orch.add_agent("flaky", FakeClient, flaky, backoff_base=0.01)
    orch.start()
    # wait for the result before stopping — stop() cancels pending backoff
    result = orch.results.get(timeout=10)
    orch.stop(timeout=10)

    assert len(attempts) == 2
    assert result == ("flaky", "recovered")


def test_agent_gives_up_after_max_restarts():
    orch = Orchestrator()

    def always_fails(client, ctx):
        raise RuntimeError("boom")

    orch.add_agent("doomed", FakeClient, always_fails,
                   max_restarts=1, backoff_base=0.01)
    orch.start()
    result = orch.results.get(timeout=10)
    orch.stop(timeout=10)

    assert result == ("doomed", {"error": "max restarts exceeded"})
