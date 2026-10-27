# Chimera

Chimera is a local-first foundation for acquiring, verifying, packaging, and reusing capabilities across domains. Its core is domain-agnostic: a task specification can describe any requested outcome, while providers, runtimes, and verifiers are selected through typed contracts.

## Install and run

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
python -m chimera
```

The database defaults to `.chimera/chimera.db`; set `CHIMERA_DATA_DIR` to change it. Run `python -m pytest -q` for tests.

## Current architecture

- `contracts.py` contains domain-independent task, provider, verifier, discovery, and acquisition result contracts.
- `acquisition.py` implements a bounded observable lifecycle and deterministic fixture provider. It is not model integration.
- `discovery.py` reports available tools and runtimes.
- `models.py` defines versioned Capability Capsules and lifecycle states.
- `registry.py` stores SQLite metadata, immutable artifact versions, manifests, integrity checks, acceptance, and rollback.
- `audit.py` persists execution traces; `policy.py` validates paths and permissions.
- `supervisor.py` runs explicit subprocess commands with bounded output and timeouts.
- `ui.py` provides the native PySide6 shell.
- `local_model.py` provides an explicitly configured, local-only llama.cpp provider for GGUF models. It never contacts a hosted API or downloads weights.
- `workflow.py` connects local task analysis, proposal validation, isolated staging, independent static verification, and pending review.

Accepted artifacts are independently verified before retention. Failed or inconclusive candidates are not accepted, and repair attempts are bounded. Subprocess supervision is limited containment, not a complete security sandbox. No hosted model, model training, arbitrary application control, or universal task completion is implemented.

The acquisition workflow has explicit states: blocked, rejected, pending review, and accepted only after the existing registry acceptance path. Model proposals are treated as untrusted data. Candidate paths, file counts, file sizes, permissions, and prohibited operations are checked statically. Behavioral verification of untrusted generated code is reported as unresolved rather than executed automatically. Execution approval and acceptance approval are separate gates.

## Review and acceptance

Pending candidates are persisted under `.chimera/candidates`. Review them with `python -m chimera.review list`, then use `inspect`, `verify`, `approve-execution`, `approve-acceptance`, `accept`, `reject --reason "..."`, and `history` with the candidate ID. Acceptance rechecks the current manifest and routes promotion through `Registry.accept`; it never executes candidate code. Approval is bound to the candidate metadata and current digest. Filesystem and SQLite updates are not a single cross-resource transaction, so orphaned staging data remains recoverable for inspection.

The native Capabilities view uses the same `ReviewService` for refresh, inspection, safe verification, acceptance approval, acceptance confirmation, and rejection. Static verification is labelled separately from behavioral verification. The CLI remains authoritative and both interfaces require the same approvals; candidate code is never run by either review path.

The earlier Verilog benchmark and adapter were development fixtures, not product architecture, and have been removed from the core. Domain adapters can be added later through the generic contracts without changing the orchestrator.

## Local model configuration

Set `CHIMERA_MODEL_PATH` to a user-provided `.gguf` file and `CHIMERA_LLAMA_CLI` to a local llama.cpp executable, or select them in Settings. GUI-selected paths persist in `model_config.json` under the active Chimera data directory (`%LOCALAPPDATA%\Chimera` for packaged builds, `.chimera` from source). Explicit environment variables override persisted paths. Optional limits are `CHIMERA_MODEL_CONTEXT`, `CHIMERA_MODEL_MAX_TOKENS`, `CHIMERA_MODEL_TIMEOUT`, and `CHIMERA_MODEL_THREADS`. If either required file is absent, the provider reports `missing` or `unavailable` and does not fall back to a network service. Model weights are never downloaded automatically. Automated tests use an explicitly injected fake runner; they do not perform model inference.

Run `python -m chimera.diagnostic` to inspect readiness without printing the full environment. Run `python -m chimera.smoke_test` for genuine local inference; it exits nonzero when the runtime or model is missing and never uses the fake runner. Use `CHIMERA_MODEL_GPU_LAYERS=0` for the CPU baseline; positive values opt into llama.cpp GPU offload. `python -m chimera.benchmark` is not a CLI yet, but the reporting API is available through `chimera.benchmark.run` and writes ignored JSON reports under `.chimera/benchmarks`. Peak RAM and GPU metrics are reported as unavailable unless measurement support is added; no values are estimated.

Run a real production-path acquisition demonstration with `python -m chimera.demo_run "Create a deterministic local utility that converts Celsius to Fahrenheit" --json`. It validates the configured local runtime, performs model-driven task analysis and proposal generation, stages the candidate under `.chimera/candidates`, performs independent static verification, and records an audit row. It never executes or accepts generated code. A missing model is reported as `blocked`; no fake success is substituted. After a successful run, inspect with `python -m chimera.review inspect <candidate-id>`, verify again, approve acceptance explicitly, and then accept it through `python -m chimera.review accept <candidate-id>`.

## Windows build and launch

Install the build dependency once with `python -m pip install pyinstaller`, then run `powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1`. The script runs tests and compilation checks, builds a one-folder distribution, validates `dist\Chimera\Chimera.exe`, and only then replaces the previous distribution. Launch it with `scripts\run_chimera.bat`, or use `scripts\build_and_run.bat` to build and open the newest validated app. `scripts\run_chimera_dev.bat` launches from source.

Packaged writable state defaults to `%LOCALAPPDATA%\Chimera`; source runs continue using `.chimera` unless `CHIMERA_DATA_DIR` is set. Model weights and llama.cpp remain external through `CHIMERA_MODEL_PATH` and `CHIMERA_LLAMA_CLI`; they are never bundled. The build output is intentionally git-ignored. The GUI uses Flet `1.0.5` in native desktop mode; it is not a browser UI.

The current visual system is monochrome (`#050505` through `#F2F2F2`) with no colored accents. The bundled font source is `fonts/ec-dots-life-font/EcDotsLifeRegular-AydL.ttf` (“EC Dots Life”); its included metadata marks it CC BY-NC-SA, so review its license before redistributing a packaged build. Manim 0.22.0 renders the startup animation from `scripts/animation/chimera_intro.py` into `assets/chimera_intro.gif`; Manim is build-time only and is not imported by the application. The full-window intro runs for 2.5 seconds by default and can be skipped with `CHIMERA_INTRO=0`.
