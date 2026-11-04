# Chimera Roadmap

> **Last updated:** 2026-11-04
> **Current version:** Phase 5.3.4 — Scoped Windows App Control & Canvas Interaction

Chimera is an open, local-first AI agent platform. This roadmap describes its engineering direction and capability maturity across eight strategic pillars.

---

## Pillars

| # | Pillar | Current Maturity | Target |
|---|--------|-----------------|--------|
| A | General Intelligence & Agent Orchestration | 🟡 Foundation | Full multi-step planning + dependency graph |
| B | Model Provider Agnosticism | 🟢 Complete | Stable (all protocols covered) |
| C | Computer Use & Desktop Automation | 🟡 Foundation | Cross-app automation with governed approval |
| D | Development Tool Integration | 🟡 Foundation | IDE, debugger, version control, CI |
| E | Application Ecosystem | 🟡 Foundation | 20+ certified connectors |
| F | Cybersecurity Research Tools | 🟡 Foundation | Governed Ghidra + network analysis |
| G | Self-Learning & Workflow Memory | 🟡 Foundation | Approval-gated skill promotion + replay |
| H | Governance, Safety & Auditability | 🟢 Strong | Continuous hardening |

**Maturity key:** 🔴 Not started · 🟠 Planned · 🟡 Foundation · 🟢 Complete

---

## Phase Milestones

### ✅ Phase 4.5 — Universal Model Provider Architecture
- 8-adapter provider registry (OpenAI, OpenAI Responses, Anthropic, Gemini x2, Meta, llama.cpp, Custom)
- Provider Compatibility Lab with 5-level progressive assessment
- Adapter SDK (`chimera_provider_sdk/`) with type validation and reference implementation
- SSRF remediation: DNS rebinding protection, 0.0.0.0 rejection, IPv4-mapped IPv6 blocking
- Synthetic benchmark suite + failure injection tests
- **Outcome:** 161 tests passing, provider independence proven

### ✅ Phase 5.0 — General-Purpose Agent Platform Foundation
- Application Integration SDK (`chimera/applications/`) with manifest, risk levels, and governance
- GhidraConnector: headless binary analysis, PE/ELF string extraction, function enumeration

- ExperienceLearningEngine: skill extraction from traces, approval lifecycle, secret redaction
- **Outcome:** 166 tests passing, application platform foundation established

### ✅ Phase 5.2 — Reverse Engineer Anything (REA)
|- REA: General-purpose reverse-engineering intelligence domain
|- Artifact detection: PE, ELF, Mach-O, .NET, APK, DEX, firmware, archives
|- Built-in static analysis: zero-dependency PE/ELF parsers, strings, entropy, exports/imports
|- Backend architecture: modular adapters for Ghidra, radare2, Rizin
|- Security controls: read-only analysis, size limits, path validation
|- Normalized results: evidence provenance, confidence scoring, structured findings
|- Application SDK integration: REA actions governed through AgentRuntime
|- **Outcome:** REA as first-class capability, Ghidra preserved as specialized backend

---

### 🟡 Phase 5.3 — Cross-Capability Agentic Workflows (implemented foundation; CI: 204 passed, 2 skipped)
- Dependency-aware sequential workflow templates across workspace and REA
- Each step routed through AgentRuntime tool validation and permission enforcement
- Protected artifact writes pause for explicit operator approval and resume
- Result verification and truthful completed, failed and blocked states
- Synthetic workflow-executor success removed; no automatic approval
- Model-driven planning, general-purpose replanning and parallel execution remain planned

### Phase 5.3.1 — First-Run Agent Workspace & Local Runtime Verification
- Persistent chat workspace with conversation history, resumable sessions, and first-run prompt suggestions
- Inline user/assistant timeline, visible tool-result cards, active-task indicator, stop control, and exact-action approval cards
- Chat opens directly by default; intro animation is opt-in
- Local-model status distinguishes configured assets from a verified inference path
- A local-only self-test exercises the active conversation model's structured AgentRuntime decision protocol; hosted profiles are rejected by this check
- Linux and Windows CI matrix for the automated test suite
- Desktop control uses exact process authorization and supported UI Automation patterns; a scoped foreground-only input broker handles bounded pointer/keyboard actions for observed UIA targets, with explicit approval and post-action evidence.

### Phase 5.3.3 — Oxide Agent Workbench & Persistent Local Runtime
- Three-panel continuous chat workspace inspired by coding-agent workbenches: thread/tool rail, conversation composer, and live session-intelligence rail
- Oxide / Fieldwork visual system uses warm graphite, parchment, signal-lime and kiln-coral instead of blue-violet AI styling
- Persistent `llama-server` model is loaded at app startup and kept resident; server health and model readiness are visible in the workbench
- Runtime choice is strict: a selected server failure stops the request; there is no silent server-to-CLI, model-to-model, or hosted fallback
- CLI-only inference remains an explicit one-shot mode and is labelled as such
- Model Bay persists GGUF/runtime paths and context, output-token, thread and GPU-layer tuning; Q4_K_M is documented as a balanced starting point for compact models
- CI runs the automated test suite on Windows and Linux and builds a portable Windows desktop bundle
- Scope remains bounded by installed connectors and governed tools; universal support for every desktop task is not claimed

### Phase 5.3.2 — Governed Blender Scene-Creation Connector
- Discover a locally installed Blender executable without downloading binaries or add-ons
- Accept a bounded JSON scene recipe instead of model-authored Python
- Create primitive-based .blend projects and PNG previews through a fixed background script
- Require Build Mode and exact operator approval before writing either artifact
- Verify both output files after Blender exits; automated connector tests mock the Blender process
- Scope limitation: primitive scene assembly only; general modelling, advanced materials, animation and unrestricted GUI control remain future work

### Phase 5.3.4 — Scoped Windows App Control & Canvas Interaction

- [x] Add foreground-scoped pointer and keyboard input grounded in fresh UI Automation element rectangles, exact HWND/PID identity, operator approval and per-packet foreground checks.
- [x] Add multi-stroke drawing: up to 16 separate pen-up paths and 256 normalized points in one approved action.
- [x] Capture before/after pixel evidence and abort safely on foreground or process identity changes.
- [x] Bind an approved application launch to the exact visible HWNDs observed by the launcher; restrict later observations and actions to those HWNDs.
- [x] Continue the local agent loop after approval when a launch is successfully authorized; expose canvas interaction early in the compact-model tool catalogue.
- [ ] Run live Windows acceptance for Paint: identify its real canvas element, draw disconnected shapes, save a PNG, reopen it and verify its dimensions/content.
- [ ] Add local vision-capable observation for semantic screenshot understanding; pixel-diff alone is not semantic correctness.
- [ ] Expand app-specific adapters for Office, editors, file managers, graphics tools, CAD/EDA and media tools.

**Acceptance boundary:** Automated tests validate the scoped input and authorization contracts. They do not prove that every installed Windows application or Paint workflow succeeds on a user's desktop. Application/version/action support must be certified with live acceptance evidence.

## Near-Term (Phase 5.4)

**Theme: Workspace Agent Maturity and Persistent Operator Goals**

- [x] Persistent per-turn task checklist with stable item IDs, explicit statuses, SQLite persistence, and live workbench rendering
- [ ] Persistent task context across sessions (resume interrupted jobs and continue the same multi-turn task)
- [x] Operator-owned goal and ambition ledger: local SQLite storage, active/paused/completed/archived states, priorities, success criteria and evidence references
- [x] Distinguish long-term goals, project milestones and individual task runs; completion requires operator-supplied evidence and is never inferred from an assistant message or task state
- [x] Attach bounded active/paused goal summaries to local model reasoning as ephemeral read-only context; do not send persistent goals to hosted providers by default
- [ ] Add semantic, project-scoped goal retrieval and operator-controlled context sharing for hosted models
- [ ] Dependency-aware plan visualization in UI (task graph, prerequisites, approvals, and evidence)
- [ ] Dynamic capability routing that exposes only the smallest relevant tool set to compact local models
- [ ] Parallel task execution with safety barriers
- [ ] Agent-generated sub-task delegation (spawn governed sub-agents)
- [ ] Token-budget awareness: estimate and report context costs before long tasks
- [ ] Skill lifecycle: versioned proposals, isolated regression tests, explicit promotion and rollback before reusable execution

---

### Phase 5.4.1 — Small-Model Agent Harness

- Persistent checklist state is owned by SQLite/runtime, not left only in model context.
- The model creates and updates concise todo items through registered internal tools.
- Checklist bookkeeping does not grant filesystem or application permissions.
- The workbench renders the persisted checklist.
- DOCX cleanup removes partial files only when the current invocation created them.
- A checklist is progress metadata, not proof of a requested outcome.

**Model boundary:** a 3B model can still misplan, choose the wrong tool, or report an incorrect result. The runtime improves structure and governance; it does not turn the model into a frontier model.

## Medium-Term (Phase 5.5)

**Theme: Development Workflow Integration**

- [ ] Git connector: status, diff, stage, commit (approval-gated), branch management
- [ ] Shell/terminal connector: command execution with sandboxed subprocess isolation
- [ ] Python/Node REPL connector: governed code execution with output capture
- [ ] File-watcher integration: trigger agent analysis on file change events
- [ ] Dependency vulnerability scanning (pip-audit / npm audit wrappers)

---

## Medium-Term (Phase 5.6)

**Theme: Application Ecosystem Expansion**

Priority connectors (each requires: discover → inspect → modify → approval chain):

- [ ] VS Code connector (extension API or LSP)
- [ ] Browser connector (headless Chrome via CDP)
- [ ] Obsidian connector (vault file operations, note linking)
- [x] Blender connector foundation: bounded primitive-scene generation and PNG render preview; broader Blender API coverage remains planned
- [ ] OBS Studio connector (scene switching, source management)
- [ ] JIRA / GitHub Issues connector (read-only first, write with approval)
- [ ] KiCad/EDA connector for PCB projects, schematic inspection and design-rule-check workflows
- [ ] LibreOffice Writer/Calc/Impress connector for governed document, spreadsheet and presentation workflows
- [ ] Graphics-editor connector for bounded image and layer operations

---

## Long-Term (Phase 6.0)

**Theme: Autonomous Capability & Self-Improvement**

- [ ] Skill library with versioning, provenance, and rollback
- [ ] Automated regression testing of promoted skills
- [ ] Operator-defined capability profiles (restrict surface by project/context)
- [ ] Natural-language policy engine (operator describes what is and isn't allowed)
- [ ] Federated skill sharing (opt-in export/import of anonymized workflows)
- [ ] Multi-modal inputs: screen capture analysis, audio transcription, image understanding

---

## Governance Principles (Permanent)

These constraints are non-negotiable at every phase:

1. **No silent cloud fallback.** Model selection is always explicit and operator-controlled.
2. **No capability escalation without approval.** Every write, render, or execute action requires either pre-authorization or real-time operator approval.
3. **No unscoped input injection.** No PyAutoGUI, clipboard injection, background-window input, or global hooks. Windows SendInput is permitted only inside the approved scoped broker: fresh UIA target, exact HWND/process identity, target bounds contained in the authorized window, exact foreground ownership before every event, bounded event sequences, and post-action evidence.
4. **No automatic commits or pushes.** All VCS operations require explicit operator confirmation.
5. **No model downloads without consent.** The operator must explicitly authorize any model acquisition.
6. **Audit trail always on.** Every tool invocation, approval decision, and provider call is logged with provenance.
7. **SSRF protection always active.** Provider endpoint validation enforced for every outbound request.
8. **Local-first by default.** Remote inference only when operator chooses a remote provider.

---

## Capability Maturity Matrix

| Capability | Implemented | Tested | Governed | Production-Ready |
|-----------|-------------|--------|----------|-----------------|
| Multi-provider inference | ✅ | ✅ | ✅ | ✅ |
| Local GGUF inference | ✅ | ✅ | ✅ | ✅ |
| Provider Compatibility Lab | ✅ | ✅ | ✅ | ✅ |
| Adapter SDK | ✅ | ✅ | ✅ | ✅ |
| SSRF protection | ✅ | ✅ | ✅ | ✅ |
| Workspace read tools | ✅ | ✅ | ✅ | ✅ |
| Workspace write tools (governed) | ✅ | ✅ | ✅ | ✅ |
| Task policy enforcement | ✅ | ✅ | ✅ | ✅ |
| Application connector SDK | ✅ | ✅ | ✅ | 🟡 |
| Ghidra static analysis | ✅ | ✅ | ✅ | 🟡 (needs Ghidra installed) |
| Experience learning | ✅ | 🟡 | ✅ | 🟡 (proposals, not executable replay) |
| Cross-capability sequential workflows | ✅ | ✅ | ✅ | 🟡 |
| Parallel task execution | 🔴 | 🔴 | 🟡 | 🔴 |
| Git connector | 🔴 | 🔴 | 🟡 | 🔴 |
| Browser connector | ✅ | ✅ | ✅ | 🟡 (requires local browser/runtime) |
| Skill library versioning and approved replay | 🔴 | 🔴 | 🟡 | 🔴 |

**Key:** ✅ Done · 🟡 Partial/Foundation · 🔴 Not started

---

## Contributing a Connector

See [`docs/provider_integration_guide.md`](docs/provider_integration_guide.md) for the provider adapter SDK.

For application connectors, implement `BaseApplicationConnector` from `chimera/applications/base.py`:

```python
from chimera.applications.base import BaseApplicationConnector
from chimera.applications.manifest import ApplicationManifest, ApplicationAction, ActionRiskLevel

class MyAppConnector(BaseApplicationConnector):
    def discover(self) -> ApplicationManifest: ...
    def execute_action(self, action_id, arguments, approved=False) -> dict: ...
```

Register via `ApplicationRegistry.register(MyAppConnector())`.

---

*Chimera is a local-first platform. The roadmap reflects what can be built without cloud dependencies, proprietary licenses, or surveillance infrastructure.*
