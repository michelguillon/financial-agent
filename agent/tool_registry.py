"""tool_registry.py — assembles the Anthropic tools list and the
name -> Python-callable dispatch table for the agent loop.

Each tool module exports two things:
  - `SCHEMAS`: list of JSON-Schema definitions for the Anthropic API
  - the implementation functions themselves

This module imports both, validates the shapes, and exposes:
  - `ANTHROPIC_TOOLS`: pass directly to `client.messages.create(tools=...)`
  - `TOOL_FUNCTIONS`: {name: callable} — the agent loop dispatches tool_use
    blocks against this dict.

Single source of truth — both API and dispatch derive from the same per-module
SCHEMAS list, so they can't drift apart.
"""

from __future__ import annotations

from agent.tools import classification, scenarios, state

# Modules in registration order. Within each module, SCHEMAS lists the tools
# in the order their definitions appear in code.
_TOOL_MODULES = (state, classification, scenarios)


# ---------------------------------------------------------------------------
# Assemble
# ---------------------------------------------------------------------------

ANTHROPIC_TOOLS: list[dict] = []
TOOL_FUNCTIONS: dict[str, callable] = {}


def _validate_schema(schema: dict, module_name: str) -> None:
    """Cheap shape check — catches typos before the API does."""
    for key in ("name", "description", "input_schema"):
        if key not in schema:
            raise ValueError(
                f"{module_name}: tool schema missing required key {key!r}: {schema}"
            )
    if not isinstance(schema["input_schema"], dict):
        raise ValueError(
            f"{module_name}: tool {schema['name']!r} input_schema must be a dict"
        )
    if schema["input_schema"].get("type") != "object":
        raise ValueError(
            f"{module_name}: tool {schema['name']!r} input_schema.type must be 'object'"
        )


for module in _TOOL_MODULES:
    for schema in module.SCHEMAS:
        _validate_schema(schema, module.__name__)
        name = schema["name"]

        if name in TOOL_FUNCTIONS:
            raise ValueError(f"Duplicate tool name across modules: {name!r}")

        func = getattr(module, name, None)
        if func is None or not callable(func):
            raise ValueError(
                f"{module.__name__}: schema names {name!r} but module has no "
                "matching callable. Add the function or rename the schema."
            )

        ANTHROPIC_TOOLS.append(schema)
        TOOL_FUNCTIONS[name] = func


def dispatch(tool_name: str, tool_input: dict) -> object:
    """Invoke a registered tool by name.

    Raises KeyError if the name isn't registered. Argument validation is the
    tool function's own responsibility.
    """
    if tool_name not in TOOL_FUNCTIONS:
        raise KeyError(
            f"Unknown tool: {tool_name!r}. "
            f"Registered: {sorted(TOOL_FUNCTIONS)}"
        )
    return TOOL_FUNCTIONS[tool_name](**tool_input)


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"Registered {len(ANTHROPIC_TOOLS)} tools:")
    for s in ANTHROPIC_TOOLS:
        n_args = len(s["input_schema"].get("properties", {}))
        required = len(s["input_schema"].get("required", []))
        print(f"  {s['name']:<35} args={n_args} required={required}")

    # Verify every schema name maps to a real callable.
    for s in ANTHROPIC_TOOLS:
        assert s["name"] in TOOL_FUNCTIONS, f"missing dispatch entry: {s['name']}"
        assert callable(TOOL_FUNCTIONS[s["name"]])

    # Verify dispatch works for a read-only tool that doesn't need the API.
    result = dispatch("get_unclassified_transactions", {"limit": 3})
    assert isinstance(result, list)
    print(f"\ndispatch('get_unclassified_transactions', limit=3) -> {len(result)} rows")

    # Unknown tool raises clearly.
    try:
        dispatch("nope_not_a_tool", {})
    except KeyError as e:
        print(f"dispatch('nope_not_a_tool') -> KeyError (expected): {e}")

    print("\nAll tool_registry.py smoke tests passed.")
