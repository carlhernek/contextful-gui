# contextful-sidecar

The Python sidecar for Contextful. It speaks newline-delimited JSON (NDJSON) over stdin/stdout
and runs a turn-based agent loop per analysis module against OpenRouter.

## Develop

```bash
uv sync
uv run python -m contextful_sidecar     # starts the NDJSON server on stdio
```

## Test

```bash
uv run python tests/smoke.py            # offline smoke harness (no network)
uv run pytest                           # unit invariants
```

## Build (frozen binary)

```bash
uv run python build.py                  # PyInstaller -> ../src-tauri/binaries/
```

See [the spec](../../../spec/contextful-spec.md) sections 3-9 and 14.
