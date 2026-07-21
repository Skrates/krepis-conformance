# krepis-conformance

The Krepis kernel-family conformance suite (KRA-752 §10). Each kernel runs this
suite in its own CI; the family standard is pinned mechanically so the six
kernels — zygos · Obolos · Apotheke · chreos · Misthos · Taxis — cannot
re-drift on the seams a unified product surface touches.

**Kernel-family-owned.** Zero sokrates-workspace dependencies, zero framework
dependencies: the package depends only on `pytest`, `httpx`, and `pyyaml`.
`fastapi`, `fastapi_mcp`, `morphe_surface`, and `starlette` are imported lazily
from the consuming kernel's own environment, so this package can never drag a
version pin into a kernel.

## What the suite asserts

| § | Check | Mechanism |
|---|-------|-----------|
| 1 | `auth_mode ∈ {disabled, bearer}`, default `disabled` | settings-class field introspection |
| 2 | env prefix == kernel name (`ZYGOS_`, never `BD_`) | settings `model_config` |
| 3 | `fastapi_mcp` mounted at `/mcp` in the shared `_PrunedFastApiMCP` shape (subclass overriding `setup_server`); handle on `app.state.mcp` | live app inspection |
| 4 | exactly one Morphe dep, pinned to the family tag (`py-v0.7.0`, no raw commits); `EXPECTED_GRAMMAR_VERSION is GRAMMAR_VERSION` (identity — dynamic-from-package, never a literal) | pyproject regex + module import |
| 5 | every parameterized route roots at `/orgs/{org_id}`; static discovery paths are free | live OpenAPI paths |
| 6 | the `EventStore` protocol declares `initialize_schema()` **and** `close()` | protocol introspection |
| 7 | auth failures are structured problems: 401 + `code: ERR-UNAUTHORIZED` + `WWW-Authenticate: Bearer`, and the configured token is actually accepted | behavioural, in-memory app + TestClient |
| 8 | `deploy/coolify.yml` pins an immutable digest (literal `@sha256` or required `${VAR:?…digest…}`), never `:latest`; `compose.yml` declares a healthcheck; the kernel binds its registry port and nobody else's | YAML parsing |
| 9 | `integrations/sokrates` bundle completeness: `*source.yaml` + `specs/*.openapi.json` + `recipes/` + `compose.*.yml` + `README.md` (the Taxis template) | tree checks |
| 11 | governed-read selector convention (KRA-780): any privileged-read-shaped query param (pii/unmask/reveal/plaintext/decrypt/sensitive) is spelled exactly `include_pii` — the one name the public Morphe viewer strips fail-closed at its forwarding choke point (morphe #59), so a future source cannot silently opt out of the edge guard | live OpenAPI query-param scan |
| Temporal | operation-complete effective date (KRA-779): every GET tagged `surfaces` declares exactly one optional `as_of` query parameter with a string/date schema; retired names are forbidden while sequence/cursor axes remain independent; every operation proves real signed behavior at two dates | live OpenAPI equality + fresh memory app + TestClient |

Port registry (§8): zygos 8200 · Obolos 8201 · Apotheke 8202 · chreos 8203 ·
Misthos 8204 · Taxis 8205. Adding a kernel or moving a port is a family
decision made in `krepis_conformance/registry.py` — as is bumping the family
Morphe tag (`FAMILY_MORPHE_TAG`): one edit here plus a conformance release
turns every kernel's CI red until it follows.

## Adopting in a kernel

1. Add the dev dependency (uv git dependency, pinned to a tag once released):

   ```toml
   [dependency-groups]
   dev = [
       "krepis-conformance @ git+https://github.com/RationallyPrime/krepis-conformance.git@v0.4.0",
   ]
   ```

2. Declare the profile in `tests/conftest.py`:

   ```python
   from pathlib import Path

   import pytest

   from krepis_conformance import KernelProfile

   from taxis.api.app import create_app
   from taxis.settings import Settings
   from taxis.storage.repository import EventStore
   from tests.temporal_conformance import TEMPORAL_PROBES, prepare_temporal_app


   @pytest.fixture
   def kernel_profile() -> KernelProfile:
       def build_app(auth_mode: str, api_token: str | None) -> object:
           settings = Settings(
               environment="test",
               storage_backend="memory",
               auth_mode=auth_mode,
               api_token=api_token,
           )
           return create_app(settings)

       return KernelProfile(
           kernel_name="taxis",
           repo_root=Path(__file__).resolve().parents[1],
           settings_cls=Settings,
           build_app=build_app,
           store_protocol=EventStore,
           surfaces_module="taxis.surfaces",
           temporal_probes=TEMPORAL_PROBES,
           prepare_temporal_app=prepare_temporal_app,
       )
   ```

3. Add `tests/test_conformance.py`:

   ```python
   from krepis_conformance.suite import *  # noqa: F401,F403
   ```

That's it — `just test` (and therefore `just check` / CI) now runs the family
suite alongside the kernel's own tests.

### Profile contract

| Field | Meaning |
|---|---|
| `kernel_name` | lowercase family name; must exist in the port registry |
| `repo_root` | the kernel repo checkout root (tree checks run against it) |
| `settings_cls` | the pydantic-settings class (§1/§2 introspection) |
| `build_app` | `(auth_mode, api_token) -> app` — fresh, **memory-backed**, MCP handle on `app.state.mcp` |
| `store_protocol` | the `EventStore` `Protocol` class (§6) |
| `surfaces_module` | dotted module exporting `EXPECTED_GRAMMAR_VERSION` (§4) |
| `temporal_probes` | **required tuple** with exactly one `TemporalProbe` per live `surfaces` GET `operationId`; its set must match OpenAPI exactly |
| `prepare_temporal_app` | **required callback** `(app, TestClient) -> Mapping[str, str]`; installs a runtime test signer, seeds through real append endpoints, and returns values for generated OpenAPI path parameters |
| `protected_probe_path` | a real authenticated GET route (default `/orgs`) for the §7 behavioural probe |
| `deviations` | declared, reasoned departures — see below |

### Temporal probes (required since v0.4.0)

The profile contract is intentionally backwards-incompatible. An omitted or
empty temporal probe tuple fails construction, and the temporal section has no
deviation enum. Adding or removing a `surfaces` GET therefore turns CI red
until its operation-id case is added or removed in the same change.

The harness discovers each path from live OpenAPI, adds `as_of` itself, and
performs real source-v1 requests inside a fresh memory-backed app. The prepare
callback may install the kernel's runtime-only test signer and must seed a
non-empty scenario through append endpoints where the kernel exposes them. Its
returned mapping is used only to fill generated path identifiers such as
`org_id` or `book_id`; it cannot resolve, snap, or reinterpret dates.

Each probe selects exactly one proof mode:

- `DATA_DELTA`: both dates return 200; canonical signed artifact `data` changes,
  and the named later-effective sentinel is absent then present.
- `BEFORE_BIRTH`: the earlier date returns 404 and the later date returns a
  signed 200 artifact.
- `STRUCTURAL`: canonical signed `data` remains equal, but `surface_id` is
  date-addressed and changes across the two requests. Structural surfaces are
  therefore covered, not exempted.

All signed 200 artifacts must carry a runtime signature, `source_revision`, and
a `surface_id` containing the selected ISO date. Equality uses
`morphe_surface.canonical_json_bytes(artifact.data)` only—never raw response
bytes, signatures, or `produced_at` noise. Query params such as `at_sequence`,
`as_of_sequence`, and `after_sequence` may be supplied independently through a
probe's `query_params`; the harness owns `as_of`.

### Deviations — the legacy-section escape hatch

A kernel that genuinely cannot satisfy an item declares it **with a reason**;
the suite then skips that item loudly (the reason shows in pytest output).
An unreasoned deviation is rejected at profile construction; silently skipping
a check is impossible by design. The one sanctioned case today is zygos
book-scope (a book is not an org):

```python
from krepis_conformance import Deviation, KernelProfile
from krepis_conformance.profile import ConformanceItem

KernelProfile(
    kernel_name="zygos",
    ...,
    deviations=(
        Deviation(
            item=ConformanceItem.ORG_SCOPE,
            reason="a book is not an org; the kernel feed is book-scoped and the "
            "served contract documents the deviation (KRA-752 §5)",
        ),
    ),
)
```

KRA-779 temporal conformance is deliberately not a `ConformanceItem`, so no
deviation can skip it.

## Vendoring fallback

The shared-package mechanism is open to founder veto (KRA-752 §10). The suite
is deliberately vendorable wholesale: copy `registry.py`, `profile.py`,
`checks.py`, and `suite.py` into a kernel's `tests/conformance/` and the star
import keeps working — the modules have no dependencies beyond `pytest`,
`httpx`, and `pyyaml`, and all framework imports resolve from the kernel
itself.

## Developing this package

```bash
uv sync
just check   # lock-check + fmt-check + lint + ty + tests
```

The package's own tests dogfood the suite against a fake, fully conforming
kernel (`tests/conftest.py`) and mutation-test every check against the exact
drift it exists to catch (`tests/test_checks.py`).
