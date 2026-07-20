"""Family-wide constants of the Krepis standard (KRA-752).

These values ARE the standard: a family-wide change (a Morphe tag bump, a new
kernel, a port move) is one edit here plus a conformance release, and every
kernel's CI turns red until it follows. Nothing in this module may depend on
any kernel or on the sokrates workspace.
"""

from __future__ import annotations

import re
from types import MappingProxyType

FAMILY_MORPHE_TAG = "py-v0.7.0"
"""The single family-wide Morphe compiler pin (KRA-752 §4) — an immutable py-v tag."""

MORPHE_REPO_PATTERN = r"git\+https://github\.com/RationallyPrime/morphe(?:\.git)?"
"""The one sanctioned Morphe dependency source."""

FAMILY_AUTH_MODES = frozenset({"disabled", "bearer"})
"""The closed auth-mode enum (KRA-752 §1)."""

FAMILY_AUTH_DEFAULT = "disabled"
"""The family-wide auth default (KRA-752 §1); production fail-closed gates stay kernel-side."""

ORG_SCOPE_PREFIX = "/orgs/{org_id}"
"""The family parent scope (KRA-752 §5): every parameterized route roots here."""

UNAUTHORIZED_CODE = "ERR-UNAUTHORIZED"
"""The structured problem code an auth failure must carry (KRA-752 §7)."""

CONFORMANCE_BEARER_TOKEN = "krepis-conformance-probe-token"  # noqa: S105 - test-only probe value
"""Throwaway bearer credential the behavioural auth checks configure and present."""

FAMILY_PORTS: MappingProxyType[str, int] = MappingProxyType(
    {
        "zygos": 8200,
        "obolos": 8201,
        "apotheke": 8202,
        "chreos": 8203,
        "misthos": 8204,
        "taxis": 8205,
    }
)
"""The unique family port registry (KRA-752 §8). Adding a kernel is a family decision here."""

GOVERNED_READ_PARAM = "include_pii"
"""The single family spelling of a governed-read query selector (§11, KRA-780).

A governed-read selector flips a surface into a privileged representation on
the caller's authority. The public Morphe viewer refuses to forward governed
params at its one forwarding choke point, fail-closed to ``["include_pii"]``
when a source declares nothing (morphe #59) — so the edge guard holds ONLY
while every kernel spells its privileged selector exactly this way. Extending
the family's governed vocabulary is a family decision made here.
"""

GOVERNED_READ_NAME_RE = re.compile(r"pii|unmask|reveal|plaintext|decrypt|sensitive", re.IGNORECASE)
"""What a privileged-read-shaped query param name looks like (§11).

The mechanical half of the convention: any query parameter whose name matches
this pattern is treated as a governed-read selector and must be spelled
exactly :data:`GOVERNED_READ_PARAM`. A kernel that genuinely needs a second
governed selector adds it to the registry — never ships its own spelling.
"""

UNSCOPED_PATH_ALLOWLIST = frozenset(
    {
        "/healthz",
        "/openapi.json",
        "/docs",
        "/docs/oauth2-redirect",
        "/redoc",
    }
)
"""Static operational paths exempt from the org-scope rule (parameterless paths are free)."""

__all__ = [
    "CONFORMANCE_BEARER_TOKEN",
    "FAMILY_AUTH_DEFAULT",
    "FAMILY_AUTH_MODES",
    "FAMILY_MORPHE_TAG",
    "FAMILY_PORTS",
    "GOVERNED_READ_NAME_RE",
    "GOVERNED_READ_PARAM",
    "MORPHE_REPO_PATTERN",
    "ORG_SCOPE_PREFIX",
    "UNAUTHORIZED_CODE",
    "UNSCOPED_PATH_ALLOWLIST",
]
