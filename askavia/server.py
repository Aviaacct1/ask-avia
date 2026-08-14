"""The ask-avia MCP server.

Exposes the read-only tools over the streamable HTTP transport (mcp 2.x) so a Claude
connector can reach them over the network. Three properties are load-bearing:

  1. It FAILS CLOSED. config.load() raises if the bearer token is unset, and every HTTP
     request must carry `Authorization: Bearer <token>` or it is refused with 401 before
     any tool runs. The status doc's open gap was that auth was defined but unenforced;
     this is the enforcement point.
  2. It binds ONE store, read-only, at startup, and stamps which store on every audit
     entry, so no answer can come from a store the operator did not think was being read.
  3. It never writes. The tools are read-only; there is no write path.

Run:  python -m askavia.server        (reads config from the environment; see .env.example)
"""

from __future__ import annotations

import os
import secrets
import threading

from . import config as cfg
from . import store as st
from . import __version__
from .audit import AuditContext, AuditLog
from .tools import search_datapoints, get_source, compare_evidence

# The store connection is single; serialise tool calls so concurrent HTTP requests do not
# use one DuckDB connection from two threads at once. Reads are fast; the team is small.
_LOCK = threading.Lock()

# Who the audit log attributes calls to. With one shared connector token the caller is the
# registered account, not an individual; ASKAVIA_USER can name it. Refined when per-user
# identity exists.
CALLER = os.environ.get("ASKAVIA_USER", "").strip() or "ask-avia-connector"

# The query-parameter name accepted as an alternative to the Authorization header.
TOKEN_QUERY_PARAM = "token"


def build_server(store: "st.Store", audit: "AuditLog"):
    """Construct the MCPServer and register the built tools. Kept separate from serving so
    it can be exercised in a selftest without opening a port."""
    from mcp.server import MCPServer

    server = MCPServer(
        name="ask-avia",
        version=__version__,
        instructions=(
            "Read-only access to the Avia extraction store. Every figure is returned with "
            "its unit, year, class, verification status and source; nothing is a bare "
            "number. Incomparable figures are not averaged. The Benchmark folder is never "
            "read. If no evidence is held, the answer is 'no evidence held'."
        ),
    )

    @server.tool(
        name="search_datapoints",
        description=("Structured query over the store. Echoes the filters it applied and "
                     "names any it could not apply. Returns cited records."),
    )
    def search_datapoints_tool(
        metric: str | None = None,
        entity: str | None = None,
        geography: str | None = None,
        year_from: int | None = None,
        year_to: int | None = None,
        data_class: str | None = None,
        status: str | None = None,
        limit: int = 200,
    ) -> dict:
        with _LOCK:
            return search_datapoints.run(
                store, audit, user=CALLER, metric=metric, entity=entity,
                geography=geography, year_from=year_from, year_to=year_to,
                data_class=data_class, status=status, limit=limit,
            )

    @server.tool(
        name="get_source",
        description=("What a document says and where, plus the skip disclosure for the "
                     "scope ('N of M in-scope documents skipped'). Refuses quarantined "
                     "records."),
    )
    def get_source_tool(record_id: str, scope: str | None = None) -> dict:
        with _LOCK:
            return get_source.run(store, audit, user=CALLER,
                                  record_id=record_id, scope=scope)

    @server.tool(
        name="compare_evidence",
        description=("Whether figures are comparable. Aligns on code; refuses to average "
                     "across different currency, unit, scale, basis or metric, showing the "
                     "components instead."),
    )
    def compare_evidence_tool(record_ids: list[str]) -> dict:
        with _LOCK:
            return compare_evidence.run(store, audit, user=CALLER, record_ids=record_ids)

    return server


def _presented_token(request) -> str:
    """The token the caller presented, from the Authorization header if there is one, or
    from the `token` query parameter if there is not.

    The query-parameter route exists because Claude's custom-connector dialog offers only
    a name, a URL and optional OAuth client credentials. There is no field for a static
    bearer header, so a header-only service cannot be registered at all. The token
    therefore travels in the URL:

        https://ask.aviacortex.com/mcp?token=<token>

    This is deliberately weaker than a header. A URL reaches browser history, proxy and
    edge logs, and anything anyone pastes into an email or a chat. It is accepted for a
    controlled internal rollout with rotatable tokens, and is to be replaced by OAuth once
    the per-user token registry is in place. The header remains the preferred route and is
    checked first."""
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        presented = header[7:].strip()
        if presented:
            return presented
    return (request.query_params.get(TOKEN_QUERY_PARAM) or "").strip()


def _bearer_middleware(expected_token: str):
    """A Starlette middleware that rejects any request without the exact bearer token,
    before it reaches a tool. This is the enforcement of the fail-closed auth."""
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse

    def _refuse():
        return JSONResponse(
            {"error": "unauthorised",
             "detail": "ask-avia requires a valid bearer token and fails closed."},
            status_code=401,
        )

    class BearerAuth(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            if request.url.path == "/health":
                return await call_next(request)
            if not expected_token:
                return _refuse()
            presented = _presented_token(request)
            if not presented:
                return _refuse()
            # Constant-time comparison so a wrong token cannot be recovered by timing.
            # compare_digest raises TypeError on non-ASCII input; treat that as a refusal
            # rather than a 500, so the service still fails closed.
            try:
                ok = secrets.compare_digest(presented, expected_token)
            except TypeError:
                ok = False
            if not ok:
                return _refuse()
            return await call_next(request)

    return BearerAuth


def build_app(conf: "cfg.Config", store: "st.Store", audit: "AuditLog"):
    """The ASGI app: the MCP streamable-HTTP app wrapped with bearer auth and a /health
    route. Returned rather than run, so a selftest can inspect it."""
    from mcp.server.transport_security import TransportSecuritySettings

    server = build_server(store, audit)

    # DNS rebinding protection stays ON. It validates the Host header against an
    # allow-list and returns 421 on a miss, which is what stops a page in someone's
    # browser reaching a service on their own 127.0.0.1 and reading the Library. Behind
    # the Cloudflare tunnel the Host header is the PUBLIC name, so that name has to be
    # declared or every tunnelled request is refused; it comes from config, never
    # hardcoded. Origins are separate: an empty allowed_origins refuses any request that
    # carries an Origin header at all, with 403.
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=list(conf.allowed_hosts),
        allowed_origins=list(conf.allowed_origins),
    )

    app = server.streamable_http_app(transport_security=security)
    app.add_middleware(_bearer_middleware(conf.auth_token))

    async def health(_request):
        from starlette.responses import JSONResponse
        return JSONResponse({
            "status": "ok",
            "service": "ask-avia",
            "version": __version__,
            "store": store.bound.describe() if store.bound else "UNBOUND",
        })

    app.add_route("/health", health, methods=["GET"])
    return app


def main() -> None:
    import uvicorn

    # Require the bearer token (fail closed on auth) but NOT the Egnyte token: the three
    # built tools cite the stored source path and make no Egnyte call. Egnyte becomes
    # required when file_to_project is built.
    conf = cfg.load(require_secrets=False)
    if not conf.auth_token:
        raise cfg.ConfigError(
            f"{cfg.ENV_AUTH_TOKEN} is not set. The service fails closed and will not "
            f"serve without its bearer token."
        )
    print("ask-avia config:", conf.redacted())
    store = st.Store(conf)
    bound = store.bind()
    print("bound:", bound.describe())

    ctx = AuditContext(store_kind=bound.binding.kind, store_path=str(bound.store_file),
                       service_version=__version__, hostname=conf.hostname)
    audit = AuditLog(conf.audit_dir, ctx)

    app = build_app(conf, store, audit)
    print(f"serving ask-avia on {conf.hostname}:{conf.port}/mcp  (bearer auth enforced)")
    try:
        uvicorn.run(app, host="0.0.0.0", port=conf.port, log_level="info")
    finally:
        store.close()


if __name__ == "__main__":
    main()
