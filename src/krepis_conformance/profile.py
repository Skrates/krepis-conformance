"""The per-kernel conformance contract.

Each kernel declares exactly one :class:`KernelProfile` in its own test
``conftest.py`` (as a ``kernel_profile`` fixture). The profile is the ONLY
kernel-specific input the suite consumes — everything else is the family
standard in :mod:`krepis_conformance.registry`.

Deviations are the sole escape hatch: a kernel that genuinely cannot satisfy a
standard item (the sanctioned case: zygos book-scope, where a book is not an
org) declares the item WITH a reason. The suite then skips that item loudly —
the reason shows in the pytest output — instead of failing. A deviation
without a reason is rejected at profile construction; silently skipping a
check is impossible by design.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from krepis_conformance.registry import FAMILY_PORTS

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

type AppBuilder = Callable[[str, str | None], object]
"""``(auth_mode, api_token) -> ASGI app`` — a fresh, memory-backed kernel app."""


class ConformanceItem(StrEnum):
    """The deviatable seams of the KRA-752 standard (§ numbers per the ticket)."""

    AUTH_MODE = "auth-mode"  # §1 enum {disabled, bearer} + default disabled
    ENV_PREFIX = "env-prefix"  # §2 env prefix == kernel name
    MCP_SURFACE = "mcp-surface"  # §3 fastapi_mcp mount, _PrunedFastApiMCP shape
    MORPHE_PIN = "morphe-pin"  # §4 single py-v tag pin + dynamic boot assertion
    ORG_SCOPE = "org-scope"  # §5 /orgs/{org_id} parent scope
    STORE_PROTOCOL = "store-protocol"  # §6 EventStore declares close()
    AUTH_ERRORS = "auth-errors"  # §7 structured ERR-UNAUTHORIZED
    DEPLOY = "deploy"  # §8 digest-pinned image + healthcheck
    PORT = "port"  # §8 unique family port registry
    BUNDLE = "bundle"  # §9 integrations/sokrates completeness
    GOVERNED_PARAMS = "governed-params"  # §11 governed-read selector spelled include_pii


@dataclass(frozen=True, slots=True)
class Deviation:
    """One declared, reasoned departure from a standard item."""

    item: ConformanceItem
    reason: str

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise ValueError(
                f"deviation from {self.item.value!r} requires a documented reason — "
                "an unreasoned deviation is a silent skip, which the family forbids"
            )


@dataclass(frozen=True, slots=True)
class KernelProfile:
    """Everything the conformance suite needs to know about one kernel.

    ``build_app`` must return a fresh application wired to the in-memory
    backend: ``build_app("disabled", None)`` for the open probes and
    ``build_app("bearer", token)`` for the behavioural auth checks. The app is
    expected to expose its MCP handle as ``app.state.mcp`` (the family
    ``create_app`` convention).
    """

    kernel_name: str
    repo_root: Path
    settings_cls: type
    build_app: AppBuilder
    store_protocol: type
    surfaces_module: str
    protected_probe_path: str = "/orgs"
    deviations: tuple[Deviation, ...] = field(default=())

    def __post_init__(self) -> None:
        if self.kernel_name not in FAMILY_PORTS:
            known = ", ".join(sorted(FAMILY_PORTS))
            raise ValueError(
                f"kernel_name {self.kernel_name!r} is not in the family port registry ({known}); "
                "adding a kernel is a family decision made in krepis_conformance.registry"
            )
        if not self.repo_root.is_dir():
            raise ValueError(f"repo_root {self.repo_root} is not a directory")
        if not self.protected_probe_path.startswith("/"):
            raise ValueError("protected_probe_path must be an absolute route path")
        seen: set[ConformanceItem] = set()
        for deviation in self.deviations:
            if deviation.item in seen:
                raise ValueError(f"duplicate deviation declared for {deviation.item.value!r}")
            seen.add(deviation.item)

    @property
    def expected_env_prefix(self) -> str:
        """The §2 env prefix implied by the kernel name."""
        return f"{self.kernel_name.upper()}_"

    @property
    def expected_port(self) -> int:
        """The §8 registry port bound to this kernel."""
        return FAMILY_PORTS[self.kernel_name]

    def deviation_for(self, item: ConformanceItem) -> Deviation | None:
        """Return the declared deviation for ``item``, if any."""
        for deviation in self.deviations:
            if deviation.item is item:
                return deviation
        return None


__all__ = ["AppBuilder", "ConformanceItem", "Deviation", "KernelProfile"]
