"""ask-avia configuration. Every path and every secret resolves here and nowhere else.

Avia Tool Standard points 3 and 4: data lives on the workstation, read via config;
paths and secrets come from environment variables, never hardcoded.

AVIA_LOCAL_CACHE is the hinge. Provisioning a new host sets that one variable and
changes no code. One hardcoded path breaks that silently on the next host, which is
why nothing below accepts a literal drive letter.

Path resolution is by LANDMARK, never by counting folders upward. Four Meridian
modules resolved a sibling folder by going up N levels and appending a name, each
slightly differently, and one of them was the live entry point; moving the folder one
level turned `app` into `C:\\app`. Where a path is not found, the paths tried are
reported.

Auth FAILS CLOSED: the service refuses to start without its secret.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------------------
# Environment variable names. One owner per constant: these strings appear once.
# --------------------------------------------------------------------------------------

ENV_DATA_ROOT = "AVIA_LOCAL_CACHE"
ENV_STORE_PATH = "ASKAVIA_STORE_PATH"
ENV_AUTH_TOKEN = "ASKAVIA_AUTH_TOKEN"
ENV_PORT = "ASKAVIA_PORT"
ENV_HOSTNAME = "ASKAVIA_HOSTNAME"
ENV_EGNYTE_DOMAIN = "ASKAVIA_EGNYTE_DOMAIN"
ENV_EGNYTE_TOKEN = "ASKAVIA_EGNYTE_TOKEN"
ENV_AUDIT_DIR = "ASKAVIA_AUDIT_DIR"
ENV_STAGING_DIR = "ASKAVIA_STAGING_DIR"
ENV_ALLOWED_HOSTS = "ASKAVIA_ALLOWED_HOSTS"
ENV_ALLOWED_ORIGINS = "ASKAVIA_ALLOWED_ORIGINS"

DEFAULT_PORT = 8040  # 8030 is DDFS. Registered per RUN.md; see docs/PORTS.md.

# --------------------------------------------------------------------------------------
# DNS rebinding protection (mcp.server.transport_security).
#
# The MCP transport validates the Host header of every request against an allow-list and
# returns 421 "Invalid Host header" on a miss. The protection exists so that a page open
# in someone's browser cannot resolve a name to 127.0.0.1 and read the Library through
# the service running on their own machine. It stays ON.
#
# The consequence is that a public hostname in front of the service must be declared. When
# ask-avia is reached through the Cloudflare tunnel the Host header is the public name,
# not localhost, so that name goes in ASKAVIA_ALLOWED_HOSTS on the host that serves it.
# The defaults below cover a local run only, which is what a dev box and the test suite
# need.
#
# Origins are separate and sharper: an EMPTY allowed_origins list rejects any request
# carrying an Origin header at all, with 403. A caller that sends no Origin is unaffected.
# Declare the origins that may reach the service rather than discovering this as a 403.
# --------------------------------------------------------------------------------------

DEFAULT_ALLOWED_HOSTS = ("127.0.0.1", "127.0.0.1:*", "localhost", "localhost:*")
DEFAULT_ALLOWED_ORIGINS: tuple[str, ...] = ()

# --------------------------------------------------------------------------------------
# The benchmark quarantine. AIP Note 2 open issue O9: the exclusion belongs in pipeline
# CONFIGURATION, checked, not in a note. This service reads no path below.
# The golden question set AND its verified answers live here. It is the exam paper.
# --------------------------------------------------------------------------------------

EXCLUDED_CORPUS_PATHS = (
    "/Shared/Company Data/14 Avia/AI_System/AIP/Benchmark - EXCLUDED FROM CORPUS",
)

# Matched case-insensitively on a normalised path, because Egnyte paths arrive with
# mixed separators and the Dev PC is case-insensitive while the workstation is not.
_EXCLUDED_NORMALISED = tuple(
    p.replace("\\", "/").casefold().strip("/") for p in EXCLUDED_CORPUS_PATHS
)


def is_excluded(path: str) -> bool:
    """True if `path` is inside a quarantined folder. Callers must refuse, not filter."""
    if not path:
        return False
    candidate = str(path).replace("\\", "/").casefold().strip("/")
    return any(
        candidate == ex or candidate.startswith(ex + "/") for ex in _EXCLUDED_NORMALISED
    )


class ConfigError(RuntimeError):
    """Configuration is missing or unusable. Always raised with the paths tried."""


# --------------------------------------------------------------------------------------
# Store discovery, by landmark
# --------------------------------------------------------------------------------------

# Landmarks below the data root, in the order the extraction pipeline defines them.
# taxonomy v1.0 note 03 section 6: the proof stores are POST-resolve and carry the full
# schema; the pilot store is PRE-resolve with metric_code, temporality, project_id and
# entity blank. Which one is bound changes what every tool can answer, so the binding is
# reported on every start and stamped into the audit log rather than assumed.
STORE_LANDMARKS = (
    ("proof", Path("Extract") / "proof"),
    ("pilot", Path("Extract") / "pilot" / "out"),
)

REQUIRED_PIPELINE_FILES = (
    Path("Extract") / "pipeline" / "extractors" / "metric_codes.tsv",
    Path("Extract") / "pipeline" / "extractors" / "metric_entity_type.tsv",
)


@dataclass(frozen=True)
class StoreBinding:
    """What the service actually bound to, so no answer can come from a store the
    operator did not think was being read."""

    kind: str  # "proof" | "pilot" | "explicit"
    path: Path
    members: tuple[Path, ...] = field(default_factory=tuple)

    def describe(self) -> str:
        return f"{self.kind} store at {self.path} ({len(self.members)} file(s))"


@dataclass(frozen=True)
class Config:
    data_root: Path
    store: StoreBinding
    auth_token: str
    port: int
    hostname: str
    egnyte_domain: str
    egnyte_token: str
    audit_dir: Path
    staging_dir: Path
    allowed_hosts: tuple[str, ...] = DEFAULT_ALLOWED_HOSTS
    allowed_origins: tuple[str, ...] = DEFAULT_ALLOWED_ORIGINS

    def redacted(self) -> dict[str, str]:
        """Safe to print and to log. Secrets are reported as present or absent only."""
        return {
            "data_root": str(self.data_root),
            "store_kind": self.store.kind,
            "store_path": str(self.store.path),
            "store_members": str(len(self.store.members)),
            "port": str(self.port),
            "hostname": self.hostname,
            "egnyte_domain": self.egnyte_domain,
            "auth_token": "set" if self.auth_token else "MISSING",
            "egnyte_token": "set" if self.egnyte_token else "MISSING",
            "audit_dir": str(self.audit_dir),
            "staging_dir": str(self.staging_dir),
            "allowed_hosts": ", ".join(self.allowed_hosts),
            "allowed_origins": ", ".join(self.allowed_origins) or "(none)",
        }


def _require_env(name: str, why: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(
            f"{name} is not set. {why}\n"
            f"Set it in the environment (see .env.example for the names). "
            f"This service fails closed rather than starting without it."
        )
    return value


def _csv_env(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    """A comma-separated environment variable as a tuple. Unset falls back to `default`;
    set-but-empty means an explicit empty list, which is a different intent."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    items = tuple(part.strip() for part in raw.split(",") if part.strip())
    return items


def resolve_data_root() -> Path:
    raw = _require_env(
        ENV_DATA_ROOT,
        "It is the single data root: E:\\Avia on the workstation, C:\\Avia on the Dev PC.",
    )
    root = Path(raw)
    if not root.is_dir():
        raise ConfigError(f"{ENV_DATA_ROOT} is set to {root}, which is not a directory.")
    return root


def discover_store(data_root: Path) -> StoreBinding:
    """Find the store by landmark. Reports every path tried when nothing is found."""
    explicit = os.environ.get(ENV_STORE_PATH, "").strip()
    if explicit:
        path = Path(explicit)
        if not path.exists():
            raise ConfigError(
                f"{ENV_STORE_PATH} is set to {path}, which does not exist. "
                f"Unset it to fall back to landmark discovery under {data_root}."
            )
        members = (
            tuple(sorted(path.glob("*.duckdb"))) if path.is_dir() else (path,)
        )
        if not members:
            raise ConfigError(
                f"{ENV_STORE_PATH} is set to {path} but it holds no .duckdb file."
            )
        return StoreBinding(kind="explicit", path=path, members=members)

    tried: list[str] = []
    for kind, landmark in STORE_LANDMARKS:
        candidate = data_root / landmark
        tried.append(str(candidate))
        if not candidate.is_dir():
            continue
        members = tuple(sorted(candidate.glob("*.duckdb")))
        if members:
            return StoreBinding(kind=kind, path=candidate, members=members)
        parts = candidate / "store_parts"
        if parts.is_dir():
            members = tuple(sorted(parts.glob("*.parquet")))
            if members:
                return StoreBinding(kind=kind, path=parts, members=members)
        tried.append(f"{candidate} (present, no .duckdb or store_parts/*.parquet)")

    raise ConfigError(
        "No extraction store found. Paths tried, in order:\n  "
        + "\n  ".join(tried)
        + f"\nSet {ENV_STORE_PATH} to bind one explicitly, or point "
        f"{ENV_DATA_ROOT} at the data root that holds Extract\\."
    )


def load(require_secrets: bool = True) -> Config:
    """Build the configuration. Raises ConfigError with a usable message on any gap."""
    data_root = resolve_data_root()
    store = discover_store(data_root)

    if require_secrets:
        auth_token = _require_env(
            ENV_AUTH_TOKEN, "It is the bearer token every MCP caller must present."
        )
        egnyte_token = _require_env(
            ENV_EGNYTE_TOKEN, "file_to_project cannot write to Egnyte without it."
        )
    else:
        auth_token = os.environ.get(ENV_AUTH_TOKEN, "").strip()
        egnyte_token = os.environ.get(ENV_EGNYTE_TOKEN, "").strip()

    try:
        port = int(os.environ.get(ENV_PORT, "").strip() or DEFAULT_PORT)
    except ValueError as exc:
        raise ConfigError(f"{ENV_PORT} must be an integer: {exc}") from exc

    audit_dir = Path(os.environ.get(ENV_AUDIT_DIR, "").strip() or (data_root / "ask-avia" / "audit"))
    staging_dir = Path(
        os.environ.get(ENV_STAGING_DIR, "").strip() or (data_root / "ask-avia" / "staging")
    )

    return Config(
        data_root=data_root,
        store=store,
        auth_token=auth_token,
        port=port,
        hostname=os.environ.get(ENV_HOSTNAME, "").strip() or "localhost",
        egnyte_domain=os.environ.get(ENV_EGNYTE_DOMAIN, "").strip(),
        egnyte_token=egnyte_token,
        audit_dir=audit_dir,
        staging_dir=staging_dir,
        allowed_hosts=_csv_env(ENV_ALLOWED_HOSTS, DEFAULT_ALLOWED_HOSTS),
        allowed_origins=_csv_env(ENV_ALLOWED_ORIGINS, DEFAULT_ALLOWED_ORIGINS),
    )
