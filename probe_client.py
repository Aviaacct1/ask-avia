"""Round-trip probe: connect to a running ask-avia server over MCP and call the tools.

Proves the whole stack end to end (auth, transport, tools, store) without a Claude
connector. Use it on the workstation after starting the server, to see real answers from
the real store, and to shake out refinements before wiring the cloud connector.

    python probe_client.py            # http://localhost:8040/mcp, token from ASKAVIA_AUTH_TOKEN
    python probe_client.py <url> <token>

Author: Avia Solutions
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8040/mcp"
TOKEN = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("ASKAVIA_AUTH_TOKEN", "")


def _payload(result):
    """Pull the JSON a tool returned out of the MCP CallToolResult."""
    sc = getattr(result, "structuredContent", None)
    if sc:
        return sc
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            try:
                return json.loads(text)
            except Exception:
                return text
    return None


async def main() -> int:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    try:
        import httpx2 as _httpx          # mcp vendors httpx as httpx2
    except ImportError:
        import httpx as _httpx

    headers = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}
    # trust_env=False so a machine's proxy settings do not hijack a localhost call.
    http_client = _httpx.AsyncClient(headers=headers, trust_env=False, timeout=60)
    async with http_client:
        async with streamable_http_client(URL, http_client=http_client) as streams:
            read, write = streams[0], streams[1]
            async with ClientSession(read, write) as session:
                await session.initialize()

                tools = await session.list_tools()
                names = [t.name for t in tools.tools]
                print("connected. tools:", names)

                print("\n--- search_datapoints(metric='aero', limit=3) ---")
                r = await session.call_tool("search_datapoints",
                                            {"metric": "aero", "limit": 3})
                out = _payload(r)
                print("understood_as:", out.get("understood_as"))
                print("count:", out.get("count"))
                recs = out.get("records", [])
                for rec in recs[:3]:
                    print("  ", rec.get("record_id"), rec.get("metric_code"),
                          rec.get("entity"), rec.get("year"), rec.get("value"),
                          rec.get("unit"), "|", rec.get("data_class"))

                if recs:
                    rid = recs[0].get("record_id")
                    print(f"\n--- get_source(record_id={rid!r}) ---")
                    r = await session.call_tool("get_source", {"record_id": rid})
                    out = _payload(r)
                    print("locator:", out.get("locator"))
                    print("skip_disclosure:", out.get("skip_disclosure"))
                    print("citation:", out.get("citation"))

                if len(recs) >= 2:
                    ids = [recs[0]["record_id"], recs[1]["record_id"]]
                    print(f"\n--- compare_evidence({ids}) ---")
                    r = await session.call_tool("compare_evidence", {"record_ids": ids})
                    out = _payload(r)
                    print("verdict:", out.get("verdict"))
                    print("differs_on:", out.get("differs_on"))
                    print("flags:", out.get("flags"))
    print("\nround-trip OK.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
