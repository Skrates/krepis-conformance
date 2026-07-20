"""A fake, fully conforming Krepis kernel the package's own tests run against.

The fake uses REAL fastapi + fastapi_mcp (shape fidelity for the §3 check) and
a stubbed ``morphe_surface`` module (the §4 identity check needs the object,
not the real compiler). The repo tree is materialized under a session tmp dir
in the exact Taxis template shape.
"""

from __future__ import annotations

import hmac
import json
import sys
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, cast

import fastapi_mcp.server as fastapi_mcp_server
import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi_mcp import FastApiMCP
from fastapi_mcp.types import AuthConfig
from pydantic_settings import BaseSettings, SettingsConfigDict

from krepis_conformance import KernelProfile

FAKE_KERNEL_NAME = "taxis"  # borrowed registry identity; port 8205
FAKE_SURFACES_MODULE = "krepis_fake_kernel_surfaces"

# Runtime-constructed so no compile-time literal elsewhere can alias it: the §4
# check asserts identity, and the mutation tests need an equal-but-distinct str.
FAKE_GRAMMAR_VERSION = "".join(["99", ".0-conformance-test"])


# ── morphe_surface stub + fake surfaces module ─────────────────────────


@pytest.fixture(scope="session", autouse=True)
def fake_morphe_modules() -> Any:
    morphe_stub = types.ModuleType("morphe_surface")
    cast("Any", morphe_stub).GRAMMAR_VERSION = FAKE_GRAMMAR_VERSION
    surfaces_stub = types.ModuleType(FAKE_SURFACES_MODULE)
    cast("Any", surfaces_stub).EXPECTED_GRAMMAR_VERSION = FAKE_GRAMMAR_VERSION
    sys.modules["morphe_surface"] = morphe_stub
    sys.modules[FAKE_SURFACES_MODULE] = surfaces_stub
    yield morphe_stub
    sys.modules.pop("morphe_surface", None)
    sys.modules.pop(FAKE_SURFACES_MODULE, None)


# ── fake settings / store protocol / errors ────────────────────────────


class FakeSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TAXIS_", extra="ignore")

    environment: Literal["development", "test", "production"] = "development"
    auth_mode: Literal["disabled", "bearer"] = "disabled"
    api_token: str | None = None


class FakeEventStore(Protocol):
    def initialize_schema(self) -> None: ...

    def close(self) -> None: ...

    def append(self, org_id: str, payload: dict[str, Any]) -> None: ...


class FakeUnauthorized(Exception):
    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


# ── the shared _PrunedFastApiMCP shape (Taxis template, verbatim shape) ─


def _component_refs(value: Any) -> set[str]:
    refs: set[str] = set()
    if isinstance(value, dict):
        ref = value.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
            refs.add(ref.removeprefix("#/components/schemas/"))
        for child in value.values():
            refs.update(_component_refs(child))
    elif isinstance(value, list):
        for child in value:
            refs.update(_component_refs(child))
    return refs


class _PrunedFastApiMCP(FastApiMCP):
    """Work around recursive orphan ``$defs`` in FastApiMCP 0.4."""

    def setup_server(self) -> None:
        original_get_openapi = fastapi_mcp_server.get_openapi

        def get_pruned_openapi(*args: Any, **kwargs: Any) -> dict[str, Any]:
            schema = original_get_openapi(*args, **kwargs)
            components = schema.get("components")
            if isinstance(components, dict) and isinstance(components.get("schemas"), dict):
                schemas = components["schemas"]
                roots = [value for key, value in schema.items() if key != "components"]
                roots.extend(value for key, value in components.items() if key != "schemas")
                pending = (
                    set().union(*(_component_refs(root) for root in roots)) if roots else set()
                )
                reachable: set[str] = set()
                while pending:
                    name = pending.pop()
                    if name in reachable or name not in schemas:
                        continue
                    reachable.add(name)
                    pending.update(_component_refs(schemas[name]) - reachable)
                for name in set(schemas) - reachable:
                    schemas.pop(name, None)
            return schema

        fastapi_mcp_server.get_openapi = get_pruned_openapi  # ty: ignore[invalid-assignment]
        try:
            super().setup_server()
        finally:
            fastapi_mcp_server.get_openapi = original_get_openapi


# ── the fake app factory ───────────────────────────────────────────────


def build_fake_app(auth_mode: str, api_token: str | None) -> FastAPI:
    """A minimal kernel-shaped app: org routes, healthz, structured 401s, pruned MCP."""

    def require_bearer(request: Request) -> None:
        if auth_mode == "disabled":
            return
        header = request.headers.get("Authorization", "")
        scheme, _, credential = header.partition(" ")
        if (
            scheme.lower() != "bearer"
            or not credential
            or not hmac.compare_digest(credential, api_token or "")
        ):
            raise FakeUnauthorized("A valid bearer credential is required.")

    app = FastAPI(title="Fake Krepis Kernel", version="0.0.0")

    @app.exception_handler(FakeUnauthorized)
    async def unauthorized_handler(request: Request, exc: FakeUnauthorized) -> JSONResponse:
        return JSONResponse(
            status_code=401,
            content={
                "type": "https://example.invalid/problems/err-unauthorized",
                "title": "A valid bearer credential is required.",
                "status": 401,
                "code": "ERR-UNAUTHORIZED",
                "detail": exc.detail,
                "instance": request.url.path,
            },
            headers={"WWW-Authenticate": "Bearer"},
            media_type="application/problem+json",
        )

    protected = [Depends(require_bearer)]

    @app.get("/healthz", operation_id="health", tags=["ops"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/orgs", operation_id="list_orgs", dependencies=protected)
    def list_orgs() -> list[dict[str, str]]:
        return []

    @app.get("/orgs/{org_id}", operation_id="get_org", dependencies=protected)
    def get_org(org_id: str, include_pii: bool = False) -> dict[str, str]:
        # The governed drill-in: the ONE sanctioned spelling of a privileged
        # read selector (§11) — the selfcheck proves it passes as-is.
        return {"org_id": org_id, "include_pii": str(include_pii)}

    @app.get("/orgs/{org_id}/events", operation_id="list_events", dependencies=protected)
    def list_events(org_id: str) -> dict[str, str]:
        return {"org_id": org_id}

    @app.get("/surfaces/orgs", operation_id="surface_orgs", dependencies=protected)
    def surface_orgs() -> list[dict[str, str]]:
        return []

    mcp = _PrunedFastApiMCP(
        app,
        exclude_operations=["health"],
        auth_config=AuthConfig(dependencies=[Depends(require_bearer)]),
    )
    mcp.mount_http()
    app.state.mcp = mcp
    return app


# ── the fake repo tree (Taxis template shape) ──────────────────────────


def write_fake_repo_tree(root: Path, *, kernel: str = FAKE_KERNEL_NAME, port: int = 8205) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(
        "[project]\n"
        f'name = "{kernel}"\n'
        "dependencies = [\n"
        '    "fastapi>=0.115.0",\n'
        '    "morphe-grammar @ git+https://github.com/RationallyPrime/morphe.git@py-v0.7.0",\n'
        "]\n",
        encoding="utf-8",
    )
    deploy = root / "deploy"
    deploy.mkdir(exist_ok=True)
    (deploy / "coolify.yml").write_text(
        "services:\n"
        f"  {kernel}:\n"
        f"    image: ${{{kernel.upper()}_IMAGE_DIGEST:?set a published image@sha256 digest}}\n"
        "    restart: unless-stopped\n"
        "    ports:\n"
        f'      - "127.0.0.1:{port}:8000"\n',
        encoding="utf-8",
    )
    (root / "compose.yml").write_text(
        "services:\n"
        f"  {kernel}:\n"
        f"    image: {kernel}:dev\n"
        "    healthcheck:\n"
        '      test: ["CMD", "true"]\n'
        "      interval: 2s\n",
        encoding="utf-8",
    )
    bundle = root / "integrations" / "sokrates"
    (bundle / "specs").mkdir(parents=True, exist_ok=True)
    (bundle / "recipes").mkdir(parents=True, exist_ok=True)
    (bundle / f"{kernel}_source.yaml").write_text(f"source_id: {kernel}\n", encoding="utf-8")
    (bundle / "README.md").write_text(f"# {kernel} sokrates bundle\n", encoding="utf-8")
    (bundle / "specs" / f"{kernel}.openapi.json").write_text(
        json.dumps({"openapi": "3.1.0"}), encoding="utf-8"
    )
    (bundle / "recipes" / "demo.yaml").write_text("name: demo\n", encoding="utf-8")
    (bundle / f"compose.{kernel}.yml").write_text(
        f"services:\n  {kernel}:\n    image: {kernel}:dev\n", encoding="utf-8"
    )


@dataclass(frozen=True, slots=True)
class FakeKernel:
    """Everything the internal tests need to assemble (and mutate) profiles."""

    repo_root: Path


@pytest.fixture(scope="session")
def fake_kernel(tmp_path_factory: pytest.TempPathFactory) -> FakeKernel:
    root = tmp_path_factory.mktemp("fake-kernel-repo")
    write_fake_repo_tree(root)
    return FakeKernel(repo_root=root)


@pytest.fixture
def kernel_profile(fake_kernel: FakeKernel) -> KernelProfile:
    """The conforming profile — the same fixture shape a real kernel declares."""
    return KernelProfile(
        kernel_name=FAKE_KERNEL_NAME,
        repo_root=fake_kernel.repo_root,
        settings_cls=FakeSettings,
        build_app=build_fake_app,
        store_protocol=FakeEventStore,
        surfaces_module=FAKE_SURFACES_MODULE,
    )
