"""Broker adapters.

Each subpackage adapts one venue to
:class:`~execution.protocols.BrokerAdapter`. Adapters are transports: they
translate an approved order intent into a venue's dialect and translate the
response back. They hold no trading logic and no risk logic.
"""
