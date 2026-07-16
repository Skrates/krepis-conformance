"""The mechanical conformance checks behind the suite (KRA-752 §10).

Every check is a pure function ``(profile[, app]) -> None`` that raises
:class:`ConformanceError` with a kernel-actionable message. The pytest suite in
:mod:`krepis_conformance.suite` is a thin wrapper; keeping the logic here means
a founder veto of the shared-package mechanism can vendor this module (plus
``registry.py`` and ``profile.py``) into a kernel wholesale.

The checks deliberately import ``fastapi_mcp`` / ``morphe_surface`` /
``starlette`` lazily from the *consumer's* environment: the conformance package
itself carries no framework dependencies, so it can never drag a version pin
into a kernel (kernel purity — no platform, no workspace, no framework).
"""

from __future__ import annotations

import importlib
import re
import typing
from typing import TYPE_CHECKING, Any, NoReturn, cast

import yaml

from krepis_conformance.registry import (
    CONFORMANCE_BEARER_TOKEN,
    FAMILY_AUTH_DEFAULT,
    FAMILY_AUTH_MODES,
    FAMILY_MORPHE_TAG,
    FAMILY_PORTS,
    MORPHE_REPO_PATTERN,
    ORG_SCOPE_PREFIX,
    UNAUTHORIZED_CODE,
    UNSCOPED_PATH_ALLOWLIST,
)

if TYPE_CHECKING:
    from pathlib import Path

    from krepis_conformance.profile import KernelProfile


class ConformanceError(AssertionError):
    """One kernel-actionable violation of the family standard."""


def _fail(message: str) -> NoReturn:
    raise ConformanceError(message)


# ── §1 auth enum + default ─────────────────────────────────────────────


def _auth_mode_field(profile: KernelProfile) -> Any:
    fields = getattr(profile.settings_cls, "model_fields", None)
    if not isinstance(fields, dict) or "auth_mode" not in fields:
        _fail(
            f"{profile.settings_cls.__name__} must declare an `auth_mode` settings field "
            "(pydantic-settings model)"
        )
    return fields["auth_mode"]


def check_auth_mode_enum(profile: KernelProfile) -> None:
    """§1: ``auth_mode`` is the closed Literal {disabled, bearer} — never `none`."""
    annotation = _auth_mode_field(profile).annotation
    literal_args = set(typing.get_args(annotation))
    if literal_args != FAMILY_AUTH_MODES:
        _fail(
            f"auth_mode must be Literal{sorted(FAMILY_AUTH_MODES)!r}; "
            f"{profile.kernel_name} declares {sorted(map(str, literal_args))!r} "
            f"(annotation {annotation!r})"
        )


def check_auth_mode_default(profile: KernelProfile) -> None:
    """§1: the family default is ``disabled``; production fail-closed gates stay kernel-side."""
    default = _auth_mode_field(profile).default
    if default != FAMILY_AUTH_DEFAULT:
        _fail(
            f"auth_mode must default to {FAMILY_AUTH_DEFAULT!r}; "
            f"{profile.kernel_name} defaults to {default!r}"
        )


# ── §2 env prefix ──────────────────────────────────────────────────────


def check_env_prefix(profile: KernelProfile) -> None:
    """§2: the settings env prefix is the kernel's own name (no retired vocabulary)."""
    config = getattr(profile.settings_cls, "model_config", {})
    prefix = config.get("env_prefix") if isinstance(config, dict) else None
    if prefix != profile.expected_env_prefix:
        _fail(
            f"settings env_prefix must be {profile.expected_env_prefix!r}; "
            f"{profile.kernel_name} declares {prefix!r}"
        )


# ── §3 MCP surface ─────────────────────────────────────────────────────


def check_mcp_surface(profile: KernelProfile, app: object) -> None:
    """§3: fastapi_mcp mounted at ``/mcp`` in the shared ``_PrunedFastApiMCP`` shape.

    The shape is asserted structurally: the handle on ``app.state.mcp`` is a
    ``FastApiMCP`` subclass named ``_PrunedFastApiMCP`` that overrides
    ``setup_server`` (the orphan-``$defs`` pruning hook). Hand-rolled JSON-RPC
    adapters and bare ``FastApiMCP`` mounts both fail here.
    """
    fastapi_mcp = importlib.import_module("fastapi_mcp")
    state = getattr(app, "state", None)
    mcp = getattr(state, "mcp", None)
    if mcp is None:
        _fail(
            f"{profile.kernel_name} must expose its MCP handle as app.state.mcp "
            "(the family create_app convention)"
        )
    if not isinstance(mcp, fastapi_mcp.FastApiMCP):
        _fail(
            f"app.state.mcp must be a fastapi_mcp.FastApiMCP; "
            f"{profile.kernel_name} mounts {type(mcp).__name__} — hand-rolled adapters retire (§3)"
        )
    mcp_cls = type(mcp)
    if mcp_cls.__name__ != "_PrunedFastApiMCP":
        _fail(
            f"the MCP class must be the shared `_PrunedFastApiMCP` shape; "
            f"{profile.kernel_name} mounts {mcp_cls.__name__!r}"
        )
    if mcp_cls.setup_server is fastapi_mcp.FastApiMCP.setup_server:
        _fail(
            "_PrunedFastApiMCP must override setup_server with the orphan-$defs pruning wrapper; "
            f"{profile.kernel_name}'s subclass inherits it unchanged"
        )
    mounted = {str(getattr(route, "path", "")) for route in getattr(app, "routes", [])}
    if not any(path == "/mcp" or path.startswith("/mcp/") for path in mounted):
        _fail(
            f"{profile.kernel_name} must mount the MCP transport at /mcp; routes: {sorted(mounted)}"
        )


# ── §4 Morphe pin + boot assertion ─────────────────────────────────────

_MORPHE_DEP_RE = re.compile(rf"morphe[^\"']*?@\s*{MORPHE_REPO_PATTERN}@([^\"'\s]+)")


def check_morphe_pin(profile: KernelProfile) -> None:
    """§4: exactly one Morphe dependency, pinned to the family py-v tag (no raw commits)."""
    pyproject = profile.repo_root / "pyproject.toml"
    if not pyproject.is_file():
        _fail(f"{profile.kernel_name} has no pyproject.toml at {pyproject}")
    refs = _MORPHE_DEP_RE.findall(pyproject.read_text(encoding="utf-8"))
    if len(refs) != 1:
        _fail(
            f"{profile.kernel_name} must declare exactly ONE Morphe git dependency; found "
            f"{len(refs)}: {refs!r} — a kernel without the dependency takes it (§4, Misthos rule)"
        )
    if refs[0] != FAMILY_MORPHE_TAG:
        _fail(
            f"the family Morphe pin is {FAMILY_MORPHE_TAG!r}; {profile.kernel_name} pins "
            f"{refs[0]!r} (raw commits and stale tags both re-drift the family)"
        )


def check_morphe_boot_assertion(profile: KernelProfile) -> None:
    """§4: ``EXPECTED_GRAMMAR_VERSION`` is dynamic-from-package, never a hardcoded literal.

    Identity (``is``), not equality: a hardcoded string that happens to match
    today's grammar version would still drift silently on the next pin bump.
    """
    module = importlib.import_module(profile.surfaces_module)
    expected = getattr(module, "EXPECTED_GRAMMAR_VERSION", None)
    if expected is None:
        _fail(
            f"{profile.surfaces_module} must export EXPECTED_GRAMMAR_VERSION "
            "(the chreos+Obolos combined pattern, §4)"
        )
    morphe_surface = importlib.import_module("morphe_surface")
    if expected is not morphe_surface.GRAMMAR_VERSION:
        _fail(
            f"{profile.surfaces_module}.EXPECTED_GRAMMAR_VERSION must be the "
            "morphe_surface.GRAMMAR_VERSION object itself (dynamic-from-package); "
            f"got {expected!r} vs {morphe_surface.GRAMMAR_VERSION!r}"
        )


# ── §5 parent scope ────────────────────────────────────────────────────


def check_org_scope(profile: KernelProfile, app: object) -> None:
    """§5: every parameterized route roots at ``/orgs/{org_id}``.

    Parameterless discovery paths (``/orgs``, ``/surfaces/orgs``, the
    operational allowlist) are free; the moment a path carries a parameter its
    first scope must be the family parent. zygos book-scope is the sanctioned
    declared deviation.
    """
    openapi = getattr(app, "openapi", None)
    if not callable(openapi):
        _fail(f"{profile.kernel_name} app does not expose .openapi()")
    schema = cast("dict[str, Any]", openapi())
    paths: dict[str, object] = schema.get("paths", {})
    offenders = [
        path
        for path in paths
        if path not in UNSCOPED_PATH_ALLOWLIST
        and "{" in path
        and not (path == ORG_SCOPE_PREFIX or path.startswith(ORG_SCOPE_PREFIX + "/"))
    ]
    if offenders:
        _fail(
            f"parameterized routes must root at {ORG_SCOPE_PREFIX!r}; "
            f"{profile.kernel_name} serves {sorted(offenders)!r} "
            "(declare an org-scope deviation with a reason if this is a genuine design case)"
        )


# ── §6 store protocol ──────────────────────────────────────────────────


def check_store_protocol(profile: KernelProfile) -> None:
    """§6: the EventStore protocol declares its lifecycle — ``initialize_schema`` + ``close``.

    Declared on the seam (the Apotheke pattern), so composition roots own the
    lifecycle outright instead of probing with ``getattr``.
    """
    for method in ("initialize_schema", "close"):
        member = getattr(profile.store_protocol, method, None)
        if not callable(member):
            _fail(
                f"{profile.store_protocol.__name__} must declare {method}() on the protocol "
                "(duck-typed getattr probing retires, §6)"
            )


# ── §7 structured auth errors ──────────────────────────────────────────


def check_auth_errors(profile: KernelProfile, bearer_app: object) -> None:
    """§7: an auth failure is a structured problem carrying ``ERR-UNAUTHORIZED``.

    Behavioural: the bearer-mode app must 401 both a missing and a wrong
    credential with the family problem code (+ ``WWW-Authenticate: Bearer``),
    and accept the configured token — proving the probe route is genuinely
    protected rather than vacuously failing.
    """
    testclient = importlib.import_module("starlette.testclient")
    with testclient.TestClient(cast("Any", bearer_app), raise_server_exceptions=False) as client:
        for name, headers in (
            ("missing credential", {}),
            ("wrong credential", {"Authorization": "Bearer not-the-token"}),
        ):
            response = client.get(profile.protected_probe_path, headers=headers)
            if response.status_code != 401:
                _fail(
                    f"{name} on {profile.protected_probe_path} must yield 401; "
                    f"{profile.kernel_name} returned {response.status_code}"
                )
            try:
                body = response.json()
            except ValueError:
                body = None
            code = body.get("code") if isinstance(body, dict) else None
            if code != UNAUTHORIZED_CODE:
                _fail(
                    f"the 401 body must be a structured problem with code={UNAUTHORIZED_CODE!r} "
                    f"(bare HTTPException retires, §7); {profile.kernel_name} returned "
                    f"code={code!r} body={response.text[:200]!r}"
                )
            challenge = response.headers.get("WWW-Authenticate", "")
            if not challenge.startswith("Bearer"):
                _fail(
                    f"the 401 must carry `WWW-Authenticate: Bearer`; "
                    f"{profile.kernel_name} sent {challenge!r}"
                )
        authorized = client.get(
            profile.protected_probe_path,
            headers={"Authorization": f"Bearer {CONFORMANCE_BEARER_TOKEN}"},
        )
        if authorized.status_code == 401:
            _fail(
                f"the configured bearer token must be accepted on {profile.protected_probe_path}; "
                f"{profile.kernel_name} still returned 401 — the probe never authenticates"
            )


# ── §8 deploy: digest pin, healthcheck, family port ────────────────────


def _load_yaml(path: Path, *, kernel: str) -> dict[str, Any]:
    if not path.is_file():
        _fail(f"{kernel} is missing {path.name} at {path}")
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        _fail(f"{path} did not parse to a mapping")
    return loaded  # type: ignore[return-value]


def _services(document: dict[str, Any], *, path: Path) -> dict[str, dict[str, Any]]:
    services = document.get("services")
    if not isinstance(services, dict) or not services:
        _fail(f"{path} must declare at least one service")
    return cast("dict[str, dict[str, Any]]", services)


_REQUIRED_ENV_RE = re.compile(r"^\$\{[A-Z0-9_]+:\?[^}]*\}$")


def _is_digest_pinned(image: str) -> bool:
    if "@sha256:" in image:
        return True
    match = _REQUIRED_ENV_RE.fullmatch(image)
    return bool(match) and "digest" in image.lower()


def check_deploy_digest_pin(profile: KernelProfile) -> None:
    """§8: the coolify deployment consumes an immutable image digest, never a floating tag."""
    coolify_path = profile.repo_root / "deploy" / "coolify.yml"
    document = _load_yaml(coolify_path, kernel=profile.kernel_name)
    for name, service in _services(document, path=coolify_path).items():
        image = service.get("image")
        if not isinstance(image, str) or not _is_digest_pinned(image):
            _fail(
                f"service {name!r} in {coolify_path} must pin an immutable digest — either a "
                f"literal image@sha256 or a required ${{VAR:?...digest...}} interpolation "
                f"(the Apotheke pattern); got {image!r}"
            )
        if ":latest" in image:
            _fail(f"service {name!r} in {coolify_path} floats on :latest; the family forbids it")
        if "restart" not in service:
            _fail(f"service {name!r} in {coolify_path} must declare a restart policy")


def check_compose_healthcheck(profile: KernelProfile) -> None:
    """§8: the dev compose overlay exists and at least one service declares a healthcheck."""
    compose_path = profile.repo_root / "compose.yml"
    document = _load_yaml(compose_path, kernel=profile.kernel_name)
    services = _services(document, path=compose_path)
    if not any(
        isinstance(service, dict) and "healthcheck" in service for service in services.values()
    ):
        _fail(f"{compose_path} must declare a healthcheck on the kernel service (§8)")


def check_family_port(profile: KernelProfile) -> None:
    """§8: the kernel binds its registered family port in the coolify deployment."""
    coolify_path = profile.repo_root / "deploy" / "coolify.yml"
    document = _load_yaml(coolify_path, kernel=profile.kernel_name)
    expected = str(profile.expected_port)
    bound: list[str] = []
    for service in _services(document, path=coolify_path).values():
        ports = service.get("ports")
        if isinstance(ports, list):
            bound.extend(str(entry) for entry in ports)
    if not any(expected in entry for entry in bound):
        registry = " · ".join(f"{kernel} {port}" for kernel, port in sorted(FAMILY_PORTS.items()))
        _fail(
            f"{profile.kernel_name} must publish its registry port {expected} ({registry}); "
            f"{coolify_path} binds {bound!r}"
        )
    foreign = [
        f"{kernel}:{port}"
        for kernel, port in FAMILY_PORTS.items()
        if kernel != profile.kernel_name and any(f":{port}:" in entry for entry in bound)
    ]
    if foreign:
        _fail(
            f"{profile.kernel_name} binds another kernel's registry port(s) {foreign!r} — "
            "the family registry is collision-free by construction"
        )


# ── §9 integrations/sokrates bundle ────────────────────────────────────


def check_sokrates_bundle(profile: KernelProfile) -> None:
    """§9: the integrations/sokrates bundle is complete (the Taxis template shape)."""
    bundle = profile.repo_root / "integrations" / "sokrates"
    if not bundle.is_dir():
        _fail(f"{profile.kernel_name} has no integrations/sokrates bundle at {bundle}")
    missing: list[str] = []
    if not list(bundle.glob("*source.yaml")):
        missing.append("<kernel>_source.yaml (organon source config)")
    if not (bundle / "README.md").is_file():
        missing.append("README.md")
    if not list((bundle / "specs").glob("*.openapi.json")):
        missing.append("specs/<kernel>.openapi.json (OpenAPI mirror)")
    if not [p for p in (bundle / "recipes").glob("*.y*ml") if p.is_file()]:
        missing.append("recipes/*.yaml (at least one recipe)")
    if not list(bundle.glob("compose.*.yml")):
        missing.append("compose.<kernel>.yml (compose overlay)")
    if missing:
        _fail(
            f"{profile.kernel_name} integrations/sokrates bundle is incomplete; missing: "
            + "; ".join(missing)
        )


__all__ = [
    "ConformanceError",
    "check_auth_errors",
    "check_auth_mode_default",
    "check_auth_mode_enum",
    "check_compose_healthcheck",
    "check_deploy_digest_pin",
    "check_env_prefix",
    "check_family_port",
    "check_mcp_surface",
    "check_morphe_boot_assertion",
    "check_morphe_pin",
    "check_org_scope",
    "check_sokrates_bundle",
    "check_store_protocol",
]
