# Plugins: skills and extensions from Python packages

Pyharness loads skills and extensions from two sources:

1. **Filesystem scopes** — `~/.pyharness/`, `<project>/.pyharness/`,
   `<workspace>/.pyharness/`. Good for personal and project-local
   capabilities.
2. **Python entry points** — pip-installed libraries publishing skills
   or extensions through `pyproject.toml`. Good for distributing
   capabilities across teams or as open-source ecosystems.

This guide covers (2). For the filesystem layout see the
[`coding-harness` README](../../packages/coding-harness/README.md).

## Why entry points

Entry points are Python's standard plug-in mechanism (used by pytest,
flake8, setuptools, etc.). They mean:

- **Pip handles install / version / uninstall.** No bespoke registry,
  no install command, no manifest format.
- **Lazy import.** A skill's library code only loads when the model
  actually invokes `load_skill`. Listing 50 skills costs a metadata
  query, not 50 imports.
- **Namespacing for free.** Each package publishes under its own
  distribution name; pyharness prefixes plugin keys with
  `<package>:<name>` so collisions across libraries are impossible.

## Publishing a skill

```toml
# acme-finance-tools/pyproject.toml
[project]
name = "acme-finance-tools"
version = "1.0.0"

[project.entry-points."pyharness.skills"]
sec-filings = "acme.skills.sec_filings"
market-data = "acme.skills.market_data"
```

Each value is either:

- A **dotted module path** (e.g. `acme.skills.sec_filings`). Pyharness
  imports it on `load_skill` and reads its module-level
  `TOOLS = [...]` list as the skill's tools. The skill's
  description is taken from the package metadata's `Summary` field.
- A **dotted attribute** pointing at a `SkillDefinition` instance
  (e.g. `acme.skills:SEC_FILINGS_SKILL`). This lets the library
  control the body, description, and tools explicitly.

The skill is exposed to agents under the namespaced name
`acme-finance-tools:sec-filings` (the distribution name comes
before the colon).

### Authoring a skill module

```python
# acme/skills/sec_filings.py
from pyharness import Tool

class Get10KTool(Tool):
    name = "get_10k"
    description = "Fetch a 10-K filing for a given ticker."
    # ... args_schema, execute() ...

class GetEarningsTool(Tool):
    name = "get_earnings"
    description = "Fetch the latest earnings report."
    # ...

TOOLS = [Get10KTool(), GetEarningsTool()]

# Optional: `register(api)` runs when the skill activates.
# If your skill needs lifecycle hooks (e.g. attribution logging),
# put them here. Without `register`, pyharness just registers TOOLS.
def register(api):
    api.on("after_tool_call", _log_filing_access)
```

## Publishing an extension

```toml
# acme-observability/pyproject.toml
[project.entry-points."pyharness.extensions"]
pii-redactor = "acme.extensions:register_pii"
cost-tracker = "acme.extensions:register_cost_tracker"
```

Entry-point values for extensions resolve to a callable
`register(api: ExtensionAPI) -> None`:

```python
# acme/extensions.py
from pyharness import ExtensionAPI, HookOutcome

def register_pii(api: ExtensionAPI) -> None:
    api.on("before_llm_call", _redact)

async def _redact(event, ctx):
    # mutate or deny as appropriate
    return HookOutcome.cont()
```

Activated as `acme-observability:pii-redactor`.

## Activating plugins in an agent

Plugins follow the same opt-in / allowlist semantics as filesystem
extensions and skills.

### CLI / named agent

```yaml
# .pyharness/agents/research-analyst.md
---
name: research-analyst
extensions:
  - acme-observability:cost-tracker
  - acme-observability:pii-redactor
skills:
  - acme-finance-tools:sec-filings
  - market-data            # filesystem skill, no namespace
---
```

### Programmatic

```python
agent = CodingAgent(CodingAgentConfig(
    workspace=ws,
    extensions_enabled=[
        "acme-observability:cost-tracker",
    ],
    skills_enabled=[
        "acme-finance-tools:sec-filings",
        "market-data",
    ],
))
```

## Listing what's available

```python
from coding_harness import discover_extensions, discover_skills, WorkspaceContext

ctx = WorkspaceContext(workspace=Path.cwd())
print("Skills:", sorted(discover_skills(ctx).keys()))
print("Extensions:", discover_extensions(ctx.collect_extensions_dirs()).names())
```

Filesystem entries appear unprefixed; entry-point plugins appear as
`<package>:<name>`.

## Resolution rules

When a name is requested (in frontmatter or programmatic config):

1. Filesystem entries (most-specific scope wins) and entry points
   are merged into one dict keyed by name.
2. The requested name must match exactly. `market-data` and
   `acme:market-data` are different keys; use the namespaced form
   to disambiguate.
3. Unknown names are skipped with a stderr warning; they do not
   raise.

## Trust model

Entry-point plugins run arbitrary Python at import time
(extensions) or at `load_skill` time (skills). Pyharness does not
sandbox or signature-check plugins. Trust comes from your Python
environment: install via `pip install` (or `uv add`) only what you
trust, the same way you treat any Python dependency.

## Versioning and conflicts

Pip handles version constraints in your `pyproject.toml`. If two
installed packages publish the same `<package>:<name>` key (which
requires the same distribution name — extremely unlikely), pip
itself would have failed the install. In practice, namespace
collisions are not a concern.

## Discovery cost

`discover_skills()` and `discover_extensions()` only call
`importlib.metadata.entry_points(group=...)`, which reads the venv's
`*.dist-info/entry_points.txt` files. **No plugin code is imported
at discovery time.** Filesystem walks are cheap likewise. The full
discovery sweep across both sources adds negligible startup cost
even with many installed plugins.
