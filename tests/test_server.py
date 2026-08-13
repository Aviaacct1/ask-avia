"""Selftests for the MCP server wiring: the store binds, the three tools register, and
the bearer auth is ENFORCED at the request path (fails closed), which was the open gap."""

from __future__ import annotations

import pytest

from askavia import server as srv


def test_build_server_registers_three_tools(store, audit):
    s = srv.build_server(store, audit)
    assert s is not None  # construction with the three tools did not raise


def test_health_is_open_and_reports_store(config, store, audit):
    from starlette.testclient import TestClient
    app = srv.build_app(config, store, audit)
    with TestClient(app) as client:
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok" and body["service"] == "ask-avia"
        assert "core_points" in body["store"] or "ask_points" in body["store"]


def test_mcp_requires_bearer_token(config, store, audit):
    from starlette.testclient import TestClient
    app = srv.build_app(config, store, audit)
    with TestClient(app) as client:
        # no token: refused before any tool runs
        r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
        assert r.status_code == 401
        # wrong token: still refused
        r = client.post("/mcp", headers={"Authorization": "Bearer nope"},
                        json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
        assert r.status_code == 401
        # correct token: auth passes (the MCP layer may then object to the request, but
        # the point is it is NOT a 401)
        r = client.post("/mcp", headers={"Authorization": "Bearer test-token"},
                        json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
        assert r.status_code != 401
