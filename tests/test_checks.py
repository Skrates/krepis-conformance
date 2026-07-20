"""Mutation tests: every check must fail the specific drift it exists to catch."""

from __future__ import annotations

import dataclasses
import sys
import types
from pathlib import Path
from typing import Any, Literal, cast

import pytest
from conftest import (
    FAKE_GRAMMAR_VERSION,
    FakeKernel,
    build_fake_app,
    write_fake_repo_tree,
)
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic_settings import BaseSettings, SettingsConfigDict
from starlette.requests import Request

from krepis_conformance import ConformanceError, KernelProfile, checks
from krepis_conformance.profile import ConformanceItem, Deviation


def _mutated_tree(tmp_path: Path, mutate: dict[str, str]) -> Path:
    """A fresh conforming tree with named files overwritten (or removed via empty string)."""
    root = tmp_path / "mutated-kernel"
    write_fake_repo_tree(root)
    for relative, content in mutate.items():
        target = root / relative
        if content == "":
            target.unlink()
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
    return root


def _profile(kernel_profile: KernelProfile, **overrides: object) -> KernelProfile:
    return dataclasses.replace(kernel_profile, **overrides)  # type: ignore[arg-type]


# ── §1 auth ────────────────────────────────────────────────────────────


class _LegacyEnumSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TAXIS_", extra="ignore")
    auth_mode: Literal["none", "bearer"] = "none"


class _BearerDefaultSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TAXIS_", extra="ignore")
    auth_mode: Literal["disabled", "bearer"] = "bearer"


class _RetiredPrefixSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BD_", extra="ignore")
    auth_mode: Literal["disabled", "bearer"] = "disabled"


def test_legacy_none_enum_fails(kernel_profile: KernelProfile) -> None:
    profile = _profile(kernel_profile, settings_cls=_LegacyEnumSettings)
    with pytest.raises(ConformanceError, match="Literal"):
        checks.check_auth_mode_enum(profile)


def test_bearer_default_fails(kernel_profile: KernelProfile) -> None:
    profile = _profile(kernel_profile, settings_cls=_BearerDefaultSettings)
    with pytest.raises(ConformanceError, match="default"):
        checks.check_auth_mode_default(profile)


def test_missing_auth_mode_field_fails(kernel_profile: KernelProfile) -> None:
    class _NoAuthSettings(BaseSettings):
        model_config = SettingsConfigDict(env_prefix="TAXIS_", extra="ignore")

    profile = _profile(kernel_profile, settings_cls=_NoAuthSettings)
    with pytest.raises(ConformanceError, match="auth_mode"):
        checks.check_auth_mode_enum(profile)


# ── §2 env prefix ──────────────────────────────────────────────────────


def test_retired_env_prefix_fails(kernel_profile: KernelProfile) -> None:
    profile = _profile(kernel_profile, settings_cls=_RetiredPrefixSettings)
    with pytest.raises(ConformanceError, match="env_prefix"):
        checks.check_env_prefix(profile)


# ── §3 MCP ─────────────────────────────────────────────────────────────


def test_missing_mcp_handle_fails(kernel_profile: KernelProfile) -> None:
    app = FastAPI()
    with pytest.raises(ConformanceError, match=r"app\.state\.mcp"):
        checks.check_mcp_surface(kernel_profile, app)


def test_hand_rolled_mcp_adapter_fails(kernel_profile: KernelProfile) -> None:
    app = FastAPI()
    app.state.mcp = object()  # the Obolos/Apotheke hand-rolled-adapter drift
    with pytest.raises(ConformanceError, match="hand-rolled"):
        checks.check_mcp_surface(kernel_profile, app)


def test_bare_fastapi_mcp_without_pruned_shape_fails(kernel_profile: KernelProfile) -> None:
    from fastapi_mcp import FastApiMCP

    app = FastAPI()

    @app.get("/healthz", operation_id="health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    mcp = FastApiMCP(app)
    mcp.mount_http()
    app.state.mcp = mcp
    with pytest.raises(ConformanceError, match="_PrunedFastApiMCP"):
        checks.check_mcp_surface(kernel_profile, app)


def test_conforming_mcp_passes(kernel_profile: KernelProfile) -> None:
    checks.check_mcp_surface(kernel_profile, build_fake_app("disabled", None))


# ── §4 Morphe ──────────────────────────────────────────────────────────


def test_raw_commit_pin_fails(kernel_profile: KernelProfile, tmp_path: Path) -> None:
    root = _mutated_tree(
        tmp_path,
        {
            "pyproject.toml": (
                "[project]\ndependencies = [\n"
                '    "morphe-grammar @ git+https://github.com/RationallyPrime/morphe.git@3000514d",\n'
                "]\n"
            )
        },
    )
    profile = _profile(kernel_profile, repo_root=root)
    with pytest.raises(ConformanceError, match=r"py-v0\.7\.0"):
        checks.check_morphe_pin(profile)


def test_missing_morphe_dependency_fails(kernel_profile: KernelProfile, tmp_path: Path) -> None:
    root = _mutated_tree(tmp_path, {"pyproject.toml": "[project]\ndependencies = []\n"})
    profile = _profile(kernel_profile, repo_root=root)
    with pytest.raises(ConformanceError, match="exactly ONE"):
        checks.check_morphe_pin(profile)


def test_double_morphe_dependency_fails(kernel_profile: KernelProfile, tmp_path: Path) -> None:
    line = '    "morphe-grammar @ git+https://github.com/RationallyPrime/morphe.git@py-v0.7.0",\n'
    root = _mutated_tree(
        tmp_path, {"pyproject.toml": f"[project]\ndependencies = [\n{line}{line}]\n"}
    )
    profile = _profile(kernel_profile, repo_root=root)
    with pytest.raises(ConformanceError, match="exactly ONE"):
        checks.check_morphe_pin(profile)


def test_hardcoded_grammar_literal_fails(kernel_profile: KernelProfile) -> None:
    """An equal-but-distinct string (the Misthos hardcoded-literal drift) is not identity."""
    hardcoded = types.ModuleType("krepis_fake_hardcoded_surfaces")
    # Concatenating two non-empty slices allocates a new str: equal, never identical.
    lookalike = FAKE_GRAMMAR_VERSION[:2] + FAKE_GRAMMAR_VERSION[2:]
    assert lookalike == FAKE_GRAMMAR_VERSION
    assert lookalike is not FAKE_GRAMMAR_VERSION
    cast("Any", hardcoded).EXPECTED_GRAMMAR_VERSION = lookalike
    sys.modules["krepis_fake_hardcoded_surfaces"] = hardcoded
    try:
        profile = _profile(kernel_profile, surfaces_module="krepis_fake_hardcoded_surfaces")
        with pytest.raises(ConformanceError, match="dynamic-from-package"):
            checks.check_morphe_boot_assertion(profile)
    finally:
        sys.modules.pop("krepis_fake_hardcoded_surfaces", None)


def test_missing_expected_grammar_version_fails(kernel_profile: KernelProfile) -> None:
    empty = types.ModuleType("krepis_fake_empty_surfaces")
    sys.modules["krepis_fake_empty_surfaces"] = empty
    try:
        profile = _profile(kernel_profile, surfaces_module="krepis_fake_empty_surfaces")
        with pytest.raises(ConformanceError, match="EXPECTED_GRAMMAR_VERSION"):
            checks.check_morphe_boot_assertion(profile)
    finally:
        sys.modules.pop("krepis_fake_empty_surfaces", None)


# ── §5 org scope ───────────────────────────────────────────────────────


def test_foreign_parent_scope_fails(kernel_profile: KernelProfile) -> None:
    app = build_fake_app("disabled", None)

    @app.get("/employers/{employer_id}", operation_id="get_employer")
    def get_employer(employer_id: str) -> dict[str, str]:  # the Misthos drift
        return {"employer_id": employer_id}

    app.openapi_schema = None  # force regeneration after the late route
    with pytest.raises(ConformanceError, match="employers"):
        checks.check_org_scope(kernel_profile, app)


def test_static_discovery_paths_are_allowed(kernel_profile: KernelProfile) -> None:
    checks.check_org_scope(kernel_profile, build_fake_app("disabled", None))


# ── §6 store protocol ──────────────────────────────────────────────────


def test_protocol_without_close_fails(kernel_profile: KernelProfile) -> None:
    from typing import Protocol

    class _DuckStore(Protocol):
        def initialize_schema(self) -> None: ...

    profile = _profile(kernel_profile, store_protocol=_DuckStore)
    with pytest.raises(ConformanceError, match="close"):
        checks.check_store_protocol(profile)


# ── §7 auth errors ─────────────────────────────────────────────────────


def _bare_401_app(auth_mode: str, api_token: str | None) -> FastAPI:
    """The chreos drift: a 401 with no structured problem code."""
    app = FastAPI()

    @app.get("/orgs", operation_id="list_orgs")
    def list_orgs(request: Request) -> JSONResponse:
        return JSONResponse(status_code=401, content={"detail": "Unauthorized"})

    return app


def test_unstructured_401_fails(kernel_profile: KernelProfile) -> None:
    profile = _profile(kernel_profile, build_app=_bare_401_app)
    with pytest.raises(ConformanceError, match="ERR-UNAUTHORIZED"):
        checks.check_auth_errors(profile, _bare_401_app("bearer", "whatever"))


def test_unprotected_probe_fails(kernel_profile: KernelProfile) -> None:
    """A probe that never authenticates anyone must fail, not vacuously pass."""
    open_app = build_fake_app("disabled", None)  # auth off: no 401 anywhere
    with pytest.raises(ConformanceError, match="401"):
        checks.check_auth_errors(kernel_profile, open_app)


# ── §8 deploy + port ───────────────────────────────────────────────────


def test_floating_latest_image_fails(kernel_profile: KernelProfile, tmp_path: Path) -> None:
    root = _mutated_tree(
        tmp_path,
        {
            "deploy/coolify.yml": (
                "services:\n  taxis:\n    image: ghcr.io/rationallyprime/taxis:latest\n"
                "    restart: unless-stopped\n    ports:\n"
                '      - "127.0.0.1:8205:8000"\n'
            )
        },
    )
    profile = _profile(kernel_profile, repo_root=root)
    with pytest.raises(ConformanceError, match="digest"):
        checks.check_deploy_digest_pin(profile)


def test_literal_sha256_digest_passes(kernel_profile: KernelProfile, tmp_path: Path) -> None:
    root = _mutated_tree(
        tmp_path,
        {
            "deploy/coolify.yml": (
                "services:\n  taxis:\n"
                "    image: ghcr.io/rationallyprime/taxis@sha256:"
                + "0"
                * 64
                + "\n    restart: unless-stopped\n    ports:\n"
                '      - "127.0.0.1:8205:8000"\n'
            )
        },
    )
    profile = _profile(kernel_profile, repo_root=root)
    checks.check_deploy_digest_pin(profile)
    checks.check_family_port(profile)


def test_wrong_family_port_fails(kernel_profile: KernelProfile, tmp_path: Path) -> None:
    root = _mutated_tree(
        tmp_path,
        {
            "deploy/coolify.yml": (
                "services:\n  taxis:\n"
                "    image: ${TAXIS_IMAGE_DIGEST:?set a published image@sha256 digest}\n"
                "    restart: unless-stopped\n    ports:\n"
                '      - "127.0.0.1:8200:8000"\n'  # zygos's port — the chreos collision drift
            )
        },
    )
    profile = _profile(kernel_profile, repo_root=root)
    with pytest.raises(ConformanceError, match="8205"):
        checks.check_family_port(profile)


def test_missing_compose_healthcheck_fails(kernel_profile: KernelProfile, tmp_path: Path) -> None:
    root = _mutated_tree(tmp_path, {"compose.yml": "services:\n  taxis:\n    image: taxis:dev\n"})
    profile = _profile(kernel_profile, repo_root=root)
    with pytest.raises(ConformanceError, match="healthcheck"):
        checks.check_compose_healthcheck(profile)


def test_missing_deploy_file_fails(kernel_profile: KernelProfile, tmp_path: Path) -> None:
    root = _mutated_tree(tmp_path, {"deploy/coolify.yml": ""})
    profile = _profile(kernel_profile, repo_root=root)
    with pytest.raises(ConformanceError, match="coolify"):
        checks.check_deploy_digest_pin(profile)


# ── §9 bundle ──────────────────────────────────────────────────────────


def test_missing_recipes_fails(kernel_profile: KernelProfile, tmp_path: Path) -> None:
    root = _mutated_tree(tmp_path, {"integrations/sokrates/recipes/demo.yaml": ""})
    profile = _profile(kernel_profile, repo_root=root)
    with pytest.raises(ConformanceError, match="recipes"):
        checks.check_sokrates_bundle(profile)


def test_missing_bundle_readme_fails(kernel_profile: KernelProfile, tmp_path: Path) -> None:
    root = _mutated_tree(tmp_path, {"integrations/sokrates/README.md": ""})
    profile = _profile(kernel_profile, repo_root=root)
    with pytest.raises(ConformanceError, match="README"):
        checks.check_sokrates_bundle(profile)


def test_conforming_tree_passes_every_tree_check(
    fake_kernel: FakeKernel, kernel_profile: KernelProfile
) -> None:
    checks.check_morphe_pin(kernel_profile)
    checks.check_deploy_digest_pin(kernel_profile)
    checks.check_compose_healthcheck(kernel_profile)
    checks.check_family_port(kernel_profile)
    checks.check_sokrates_bundle(kernel_profile)


# ── §11 governed-read selector convention ──────────────────────────────


def _openapi_stub(paths: dict[str, Any]) -> object:
    class _Stub:
        def openapi(self) -> dict[str, Any]:
            return {"paths": paths}

    return _Stub()


def test_conforming_governed_selector_passes(kernel_profile: KernelProfile) -> None:
    # The fake kernel drills in with the ONE sanctioned spelling.
    checks.check_governed_read_params(kernel_profile, build_fake_app("disabled", None))


def test_misspelled_pii_selector_fails(kernel_profile: KernelProfile) -> None:
    app = FastAPI(title="Drifted", version="0.0.0")

    @app.get("/orgs/{org_id}/parties")
    def parties(org_id: str, show_pii: bool = False) -> dict[str, str]:
        return {"org_id": org_id}

    with pytest.raises(ConformanceError, match="show_pii"):
        checks.check_governed_read_params(kernel_profile, app)


def test_novel_privileged_selector_fails(kernel_profile: KernelProfile) -> None:
    # A privileged selector under a fresh name (never configured viewer-side)
    # is exactly the silent opt-out §11 exists to forbid.
    app = FastAPI(title="Drifted", version="0.0.0")

    @app.get("/orgs/{org_id}/ledger")
    def ledger(org_id: str, unmask: bool = False) -> dict[str, str]:
        return {"org_id": org_id}

    with pytest.raises(ConformanceError, match="unmask"):
        checks.check_governed_read_params(kernel_profile, app)


def test_path_level_governed_param_drift_fails(kernel_profile: KernelProfile) -> None:
    # Parameters merged at the path-item level are scanned too.
    stub = _openapi_stub(
        {
            "/orgs/{org_id}": {
                "parameters": [{"in": "query", "name": "reveal_plaintext"}],
                "get": {"parameters": []},
            }
        }
    )
    with pytest.raises(ConformanceError, match="reveal_plaintext"):
        checks.check_governed_read_params(kernel_profile, stub)


def test_non_query_and_benign_params_pass(kernel_profile: KernelProfile) -> None:
    stub = _openapi_stub(
        {
            "/orgs/{org_id}": {
                "get": {
                    "parameters": [
                        {"in": "path", "name": "org_id"},
                        {"in": "query", "name": "include_pii"},
                        {"in": "query", "name": "window_start"},
                        {"in": "header", "name": "x-pii-audit"},
                    ]
                }
            }
        }
    )
    checks.check_governed_read_params(kernel_profile, stub)


# ── deviations skip loudly through the suite ───────────────────────────


def test_declared_deviation_skips_with_reason(kernel_profile: KernelProfile) -> None:
    from krepis_conformance import suite

    profile = _profile(
        kernel_profile,
        deviations=(
            Deviation(
                item=ConformanceItem.ORG_SCOPE,
                reason="a book is not an org (zygos kernel feed); served contract documents it",
            ),
        ),
    )
    with pytest.raises(pytest.skip.Exception, match="a book is not an org"):
        suite.test_parameterized_routes_are_org_scoped(profile, build_fake_app("disabled", None))
