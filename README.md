# contextful-gui

The Contextful desktop app: a Tauri (Rust) shell that drives a frozen Python sidecar over
NDJSON-on-stdio, running a turn-based agent loop per analysis module against OpenRouter and
streaming events to a React UI. See [the spec](../spec/contextful-spec.md).

## Layout

```
app/
  src/                # React frontend (Vite + Tailwind v4)
  src-tauri/          # Rust core: sidecar.rs, workspace.rs, prereqs.rs, secrets.rs, settings.rs, lib.rs
  sidecar/            # Python sidecar (NDJSON server, agent loop, tools) + build.py + tests/
  scripts/            # smoke-test.ps1 / smoke-test.sh
  package.json
  src-tauri/tauri.conf.json
```

## Prerequisites

- Node 20+ and npm
- Rust toolchain (stable) + Tauri 2 prerequisites for your OS
- Python 3.12+ and `uv`
- git on PATH; ripgrep (`rg`) optional but recommended

## Develop

```bash
cd app
npm install
# create the sidecar venv (dev runs the sidecar from this venv)
cd sidecar && uv sync && cd ..
npm run tauri dev
```

## Build

```bash
cd app
npm run build:all   # builds the sidecar binary, then the Tauri app
```

## Smoke tests (release gate)

```powershell
# Windows
app\scripts\smoke-test.ps1
```
```bash
# macOS / Linux
app/scripts/smoke-test.sh
```

The NDJSON round-trip and git-worktree steps are non-negotiable gates (see spec §15).

## Template repo

The app pulls module/output-template content at runtime from `contextful-files`
(the `TEMPLATE_REPO` constant in `app/src-tauri/src/workspace.rs`). For offline dev and the
worktree smoke step, a local fixture clone is used instead of the network.
