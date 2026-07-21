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
from collections.abc import Mapping
from dataclasses import dataclass
from email.message import Message
from typing import TYPE_CHECKING, Any, NoReturn, cast
from urllib.parse import quote

import yaml

from krepis_conformance.profile import TemporalProofMode
from krepis_conformance.registry import (
    CONFORMANCE_BEARER_TOKEN,
    FAMILY_AUTH_DEFAULT,
    FAMILY_AUTH_MODES,
    FAMILY_MORPHE_TAG,
    FAMILY_PORTS,
    GOVERNED_READ_NAME_RE,
    GOVERNED_READ_PARAM,
    MORPHE_REPO_PATTERN,
    ORG_SCOPE_PREFIX,
    RETIRED_TEMPORAL_QUERY_PARAMS,
    SOURCE_SURFACE_MEDIA_TYPE,
    SURFACE_OPERATION_TAG,
    TEMPORAL_QUERY_PARAM,
    UNAUTHORIZED_CODE,
    UNSCOPED_PATH_ALLOWLIST,
)

if TYPE_CHECKING:
    from pathlib import Path

    from krepis_conformance.profile import KernelProfile, TemporalProbe


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


# ── §11 governed-read selector convention ──────────────────────────────


def check_governed_read_params(profile: KernelProfile, app: object) -> None:
    """§11: a privileged-read query selector is spelled exactly ``include_pii``.

    The public Morphe viewer never forwards governed-read params: it strips
    them at its single forwarding choke point, fail-closed to the family
    default ``["include_pii"]`` when a source declares nothing (morphe #59 —
    the fix for the live PII leak KRA-780 closed). That edge guard holds ONLY
    while every kernel spells its privileged selector exactly
    :data:`GOVERNED_READ_PARAM`: a pii-shaped query param under any other
    name rides the forward untouched and silently re-opens the leak for that
    source. Scanned over the live OpenAPI — surfaces and drill-ins alike — so
    the drift turns the kernel's own CI red before the viewer ever sees it.
    """
    openapi = getattr(app, "openapi", None)
    if not callable(openapi):
        _fail(f"{profile.kernel_name} app does not expose .openapi()")
    schema = cast("dict[str, Any]", openapi())
    offenders: list[str] = []
    for path, item in schema.get("paths", {}).items():
        if not isinstance(item, dict):
            continue
        operations = [item, *(value for value in item.values() if isinstance(value, dict))]
        for operation in operations:
            parameters = operation.get("parameters")
            if not isinstance(parameters, list):
                continue
            for parameter in parameters:
                if not isinstance(parameter, dict) or parameter.get("in") != "query":
                    continue
                name = str(parameter.get("name", ""))
                if GOVERNED_READ_NAME_RE.search(name) and name != GOVERNED_READ_PARAM:
                    offenders.append(f"{path}?{name}")
    if offenders:
        _fail(
            f"a governed-read query selector must be spelled exactly "
            f"{GOVERNED_READ_PARAM!r} — the public viewer strips only that name "
            f"(fail-closed default), so any other spelling forwards through the "
            f"edge and leaks the privileged representation; {profile.kernel_name} "
            f"serves {sorted(set(offenders))!r} (extending the governed vocabulary "
            "is a family decision in krepis_conformance.registry)"
        )


# ── Temporal: operation-complete effective-date surfaces (KRA-779) ─────


@dataclass(frozen=True, slots=True)
class _SurfaceGetOperation:
    path: str
    parameters: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class _SignedSurface:
    data: Any
    canonical_data: bytes
    surface_id: str


def _resolve_openapi_parameter(
    schema: dict[str, Any], parameter: object, *, operation_id: str
) -> dict[str, Any]:
    if not isinstance(parameter, dict):
        _fail(f"surface operation {operation_id!r} declares a non-object OpenAPI parameter")
    reference = parameter.get("$ref")
    if reference is None:
        return cast("dict[str, Any]", parameter)
    prefix = "#/components/parameters/"
    if not isinstance(reference, str) or not reference.startswith(prefix):
        _fail(
            f"surface operation {operation_id!r} uses unsupported parameter reference "
            f"{reference!r}; use a local #/components/parameters reference"
        )
    name = reference.removeprefix(prefix)
    components = schema.get("components")
    parameters = components.get("parameters") if isinstance(components, dict) else None
    resolved = parameters.get(name) if isinstance(parameters, dict) else None
    if not isinstance(resolved, dict):
        _fail(f"surface operation {operation_id!r} has unresolved parameter ref {reference!r}")
    return cast("dict[str, Any]", resolved)


def _surface_get_operations(
    profile: KernelProfile, app: object
) -> tuple[dict[str, Any], dict[str, _SurfaceGetOperation]]:
    openapi = getattr(app, "openapi", None)
    if not callable(openapi):
        _fail(f"{profile.kernel_name} app does not expose .openapi()")
    schema = openapi()
    if not isinstance(schema, dict):
        _fail(f"{profile.kernel_name} app.openapi() must return an object")
    paths = schema.get("paths")
    if not isinstance(paths, dict):
        _fail(f"{profile.kernel_name} OpenAPI document must contain a paths object")

    operations: dict[str, _SurfaceGetOperation] = {}
    for path, path_item in paths.items():
        if not isinstance(path, str) or not isinstance(path_item, dict):
            continue
        operation = path_item.get("get")
        if not isinstance(operation, dict):
            continue
        tags = operation.get("tags")
        if not isinstance(tags, list) or SURFACE_OPERATION_TAG not in tags:
            continue
        operation_id = operation.get("operationId")
        if not isinstance(operation_id, str) or not operation_id.strip():
            _fail(f"surfaces GET {path!r} must declare a non-empty operationId")
        if operation_id in operations:
            _fail(f"duplicate surfaces GET operationId {operation_id!r} in live OpenAPI")

        effective_parameters: dict[tuple[str, str], dict[str, Any]] = {}
        for owner, scope in ((path_item, "path-item"), (operation, "operation")):
            declared = owner.get("parameters")
            if declared is None:
                continue
            if not isinstance(declared, list):
                _fail(f"surface operation {operation_id!r} parameters must be an array")
            seen_in_scope: set[tuple[str, str]] = set()
            for raw_parameter in declared:
                parameter = _resolve_openapi_parameter(
                    schema, raw_parameter, operation_id=operation_id
                )
                name = parameter.get("name")
                location = parameter.get("in")
                if (
                    not isinstance(name, str)
                    or not name
                    or not isinstance(location, str)
                    or not location
                ):
                    _fail(
                        f"surface operation {operation_id!r} declares a {scope} parameter "
                        "without non-empty string `name` and `in` fields"
                    )
                identity = (name, location)
                if identity in seen_in_scope:
                    _fail(
                        f"surface operation {operation_id!r} declares duplicate {scope} "
                        f"parameter {identity!r}"
                    )
                seen_in_scope.add(identity)
                # OpenAPI operation parameters override Path Item parameters
                # with the same resolved (name, in) identity.
                effective_parameters[identity] = parameter
        parameters = tuple(effective_parameters.values())
        operations[operation_id] = _SurfaceGetOperation(path=path, parameters=parameters)
    return schema, operations


def _is_string_date_schema(schema: object) -> bool:
    if not isinstance(schema, dict):
        return False
    schema_type = schema.get("type")
    if schema_type == "string" and schema.get("format") == "date":
        return True
    if (
        isinstance(schema_type, list)
        and set(schema_type) == {"string", "null"}
        and schema.get("format") == "date"
    ):
        return True
    variants = schema.get("anyOf", schema.get("oneOf"))
    if not isinstance(variants, list):
        return False
    date_variants = [
        variant
        for variant in variants
        if isinstance(variant, dict)
        and variant.get("type") == "string"
        and variant.get("format") == "date"
    ]
    null_variants = [
        variant
        for variant in variants
        if isinstance(variant, dict) and variant.get("type") == "null"
    ]
    return len(date_variants) == 1 and len(date_variants) + len(null_variants) == len(variants)


def _validated_temporal_operations(
    profile: KernelProfile, app: object
) -> dict[str, _SurfaceGetOperation]:
    _schema, operations = _surface_get_operations(profile, app)
    for operation_id, operation in operations.items():
        query_parameters = [
            parameter for parameter in operation.parameters if parameter.get("in") == "query"
        ]
        retired = sorted(
            {
                str(parameter.get("name"))
                for parameter in query_parameters
                if isinstance(parameter.get("name"), str)
                and parameter["name"].casefold() in RETIRED_TEMPORAL_QUERY_PARAMS
            }
        )
        if retired:
            _fail(
                f"surface operation {operation_id!r} ({operation.path}) retains retired "
                f"effective-time selector(s) {retired!r}; use exactly {TEMPORAL_QUERY_PARAM!r}. "
                "Sequence axes at_sequence/as_of_sequence/after_sequence remain independent."
            )
        as_of_parameters = [
            parameter
            for parameter in query_parameters
            if parameter.get("name") == TEMPORAL_QUERY_PARAM
        ]
        if len(as_of_parameters) != 1:
            _fail(
                f"surface operation {operation_id!r} ({operation.path}) must declare exactly one "
                f"optional query parameter named {TEMPORAL_QUERY_PARAM!r}; found "
                f"{len(as_of_parameters)}"
            )
        as_of = as_of_parameters[0]
        if as_of.get("required", False) is not False:
            _fail(
                f"surface operation {operation_id!r} ({operation.path}) must make "
                f"{TEMPORAL_QUERY_PARAM!r} optional"
            )
        if not _is_string_date_schema(as_of.get("schema")):
            _fail(
                f"surface operation {operation_id!r} ({operation.path}) must declare "
                f"{TEMPORAL_QUERY_PARAM!r} as OpenAPI string/date; got {as_of.get('schema')!r}"
            )

    declared = {probe.operation_id for probe in profile.temporal_probes}
    discovered = set(operations)
    if declared != discovered:
        missing_probes = sorted(discovered - declared)
        unknown_probes = sorted(declared - discovered)
        _fail(
            "temporal probe operation-id set must equal the live OpenAPI surfaces GET set exactly; "
            f"missing probes={missing_probes!r}, unknown probes={unknown_probes!r}"
        )
    return operations


def check_temporal_surface_openapi(profile: KernelProfile, app: object) -> None:
    """KRA-779: every ``surfaces`` GET exposes exactly one optional date ``as_of``.

    This section is intentionally absent from :class:`ConformanceItem`: it is
    operation-complete and non-deviatable. The live OpenAPI operation-id set
    must equal the profile's required probe set, so a newly added pane cannot
    silently miss either schema or behavioral coverage.
    """
    _validated_temporal_operations(profile, app)


def _prepared_path_parameters(
    profile: KernelProfile, app: object, client: Any
) -> Mapping[str, str]:
    try:
        prepared = profile.prepare_temporal_app(app, client)
    except Exception as error:
        raise ConformanceError(
            f"prepare_temporal_app failed while installing the runtime signer/seeding through "
            f"append endpoints: {type(error).__name__}: {error}"
        ) from error
    if not isinstance(prepared, Mapping):
        _fail(
            "prepare_temporal_app must return a mapping of OpenAPI path-parameter names to "
            "seeded values"
        )
    if not prepared:
        _fail(
            "prepare_temporal_app returned an empty mapping; the required preparation/seed "
            "hook may not be empty or a no-op"
        )
    for name, value in prepared.items():
        if not isinstance(name, str) or not name or not isinstance(value, str) or not value:
            _fail("prepare_temporal_app path parameters must be non-empty string pairs")
    return prepared


_PATH_PARAMETER_RE = re.compile(r"\{([^{}]+)\}")


def _render_seeded_path(
    operation_id: str, operation: _SurfaceGetOperation, path_parameters: Mapping[str, str]
) -> str:
    path = operation.path
    required = _PATH_PARAMETER_RE.findall(path)
    missing = sorted(name for name in required if name not in path_parameters)
    if missing:
        _fail(
            f"prepare_temporal_app did not return seeded path parameter(s) {missing!r} "
            f"required by surface operation {operation_id!r} ({path})"
        )
    for name in required:
        path = path.replace("{" + name + "}", quote(path_parameters[name], safe=""))
    return path


def _canonical_signed_data(data: Any, *, operation_id: str) -> bytes:
    morphe_surface = importlib.import_module("morphe_surface")
    canonicalize = getattr(morphe_surface, "canonical_json_bytes", None)
    if not callable(canonicalize):
        _fail(
            "morphe_surface must export canonical_json_bytes so temporal probes compare the "
            "signed data contract, never raw response bytes"
        )
    try:
        canonical = canonicalize(data)
    except Exception as error:
        raise ConformanceError(
            f"surface operation {operation_id!r} returned data that cannot be canonicalized: "
            f"{type(error).__name__}: {error}"
        ) from error
    if not isinstance(canonical, bytes):
        _fail("morphe_surface.canonical_json_bytes must return bytes")
    return canonical


def _is_source_surface_v1_media_type(content_type: str) -> bool:
    message = Message()
    message["content-type"] = content_type
    expected_base, _, _version = SOURCE_SURFACE_MEDIA_TYPE.partition(";")
    if message.get_content_type().casefold() != expected_base.casefold():
        return False
    parameters = message.get_params(header="content-type", failobj=[], unquote=True)
    versions = [value for name, value in parameters[1:] if name.casefold() == "v"]
    return versions == ["1"]


def _signed_surface(response: Any, *, operation_id: str, selected_date: str) -> _SignedSurface:
    if response.status_code != 200:
        if response.status_code == 503:
            _fail(
                f"surface operation {operation_id!r} returned 503 for as_of={selected_date}; "
                "prepare_temporal_app must install a runtime test signer on the fresh app"
            )
        _fail(
            f"surface operation {operation_id!r} must return signed source-v1 200 for "
            f"as_of={selected_date}; got {response.status_code}: {response.text[:240]!r}"
        )
    content_type = response.headers.get("content-type", "")
    if not _is_source_surface_v1_media_type(content_type):
        _fail(
            f"surface operation {operation_id!r} returned {content_type!r}; temporal probes "
            f"require signed source-v1 {SOURCE_SURFACE_MEDIA_TYPE!r}"
        )
    try:
        body = response.json()
    except ValueError as error:
        raise ConformanceError(
            f"surface operation {operation_id!r} did not return a JSON source artifact"
        ) from error
    if not isinstance(body, dict):
        _fail(f"surface operation {operation_id!r} source artifact must be an object")
    if "data" not in body or body["data"] in (None, {}, [], ""):
        _fail(
            f"surface operation {operation_id!r} returned empty signed data; the preparation "
            "hook must seed a real scenario"
        )
    surface_id = body.get("surface_id")
    if not isinstance(surface_id, str) or not surface_id:
        _fail(f"surface operation {operation_id!r} source artifact has no surface_id")
    if selected_date not in surface_id:
        _fail(
            f"surface operation {operation_id!r} must date-address signed identity for "
            f"as_of={selected_date}; surface_id={surface_id!r}"
        )
    source_revision = body.get("source_revision")
    if not isinstance(source_revision, str) or not source_revision:
        _fail(f"surface operation {operation_id!r} source artifact has no source_revision")
    attestation = body.get("attestation")
    signature = attestation.get("signature") if isinstance(attestation, dict) else None
    if not isinstance(signature, str) or not signature:
        _fail(
            f"surface operation {operation_id!r} source artifact is unsigned; "
            "prepare_temporal_app must install a runtime test signer"
        )
    data = body["data"]
    return _SignedSurface(
        data=data,
        canonical_data=_canonical_signed_data(data, operation_id=operation_id),
        surface_id=surface_id,
    )


def _contains_named_sentinel(value: Any, sentinel: str) -> bool:
    if isinstance(value, str):
        return value == sentinel
    if isinstance(value, list):
        return any(_contains_named_sentinel(item, sentinel) for item in value)
    if isinstance(value, dict):
        return sentinel in value or any(
            _contains_named_sentinel(item, sentinel) for item in value.values()
        )
    return False


def _probe_temporal_operation(
    probe: TemporalProbe,
    operation: _SurfaceGetOperation,
    client: Any,
    path_parameters: Mapping[str, str],
) -> None:
    operation_id = probe.operation_id
    path = _render_seeded_path(operation_id, operation, path_parameters)
    earlier_date = probe.earlier_as_of.isoformat()
    later_date = probe.later_as_of.isoformat()
    base_params = list(probe.query_params)
    headers = {"Accept": SOURCE_SURFACE_MEDIA_TYPE}
    earlier = client.get(
        path,
        params=[*base_params, (TEMPORAL_QUERY_PARAM, earlier_date)],
        headers=headers,
    )
    later = client.get(
        path,
        params=[*base_params, (TEMPORAL_QUERY_PARAM, later_date)],
        headers=headers,
    )

    if probe.proof_mode is TemporalProofMode.BEFORE_BIRTH:
        if earlier.status_code != 404:
            _fail(
                f"before-birth temporal probe {operation_id!r} must change 404→200; "
                f"as_of={earlier_date} returned {earlier.status_code}"
            )
        _signed_surface(later, operation_id=operation_id, selected_date=later_date)
        return

    earlier_artifact = _signed_surface(
        earlier, operation_id=operation_id, selected_date=earlier_date
    )
    later_artifact = _signed_surface(later, operation_id=operation_id, selected_date=later_date)
    if earlier_artifact.surface_id == later_artifact.surface_id:
        _fail(
            f"surface operation {operation_id!r} reused signed surface_id "
            f"{earlier_artifact.surface_id!r} across as_of={earlier_date} and {later_date}"
        )

    if probe.proof_mode is TemporalProofMode.STRUCTURAL:
        if earlier_artifact.canonical_data != later_artifact.canonical_data:
            _fail(
                f"structural temporal probe {operation_id!r} must keep canonical signed data "
                "equal while date-addressing identity"
            )
        return

    if probe.proof_mode is TemporalProofMode.DATA_DELTA:
        sentinel = cast("str", probe.later_sentinel)
        if earlier_artifact.canonical_data == later_artifact.canonical_data:
            _fail(
                f"data-delta temporal probe {operation_id!r} returned equal canonical signed data"
            )
        if _contains_named_sentinel(earlier_artifact.data, sentinel):
            _fail(
                f"data-delta temporal probe {operation_id!r} exposes later-effective sentinel "
                f"{sentinel!r} at earlier as_of={earlier_date}"
            )
        if not _contains_named_sentinel(later_artifact.data, sentinel):
            _fail(
                f"data-delta temporal probe {operation_id!r} does not expose named "
                f"later-effective sentinel {sentinel!r} at as_of={later_date}"
            )
        return

    _fail(f"surface operation {operation_id!r} has unsupported temporal proof mode")


def check_temporal_surface_behavior(profile: KernelProfile, app: object) -> None:
    """KRA-779: prove every surface through real signed HTTP requests at two dates.

    Only canonical artifact ``data`` is compared. Signatures and ``produced_at``
    are expected to vary and are never equality inputs. Each required probe
    proves exactly one closed mode: data delta, before-birth, or structural.
    """
    operations = _validated_temporal_operations(profile, app)
    testclient = importlib.import_module("starlette.testclient")
    with testclient.TestClient(cast("Any", app), raise_server_exceptions=False) as client:
        path_parameters = _prepared_path_parameters(profile, app, client)
        for probe in profile.temporal_probes:
            _probe_temporal_operation(
                probe, operations[probe.operation_id], client, path_parameters
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
    "check_governed_read_params",
    "check_mcp_surface",
    "check_morphe_boot_assertion",
    "check_morphe_pin",
    "check_org_scope",
    "check_sokrates_bundle",
    "check_store_protocol",
    "check_temporal_surface_behavior",
    "check_temporal_surface_openapi",
]
