# Chimera Roadmap

> **Last updated:** 2026-11-03
> **Current version:** Phase 5.2 — Reverse Engineer Anything (REA)

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

## Near-Term (Phase 5.1)

**Theme: Workspace Agent Maturity**

- [ ] Persistent task context across sessions (resume interrupted jobs)
- [ ] Multi-step plan visualization in UI (dependency graph view)
- [ ] Parallel task execution with safety barriers
- [ ] Agent-generated sub-task delegation (spawn governed sub-agents)
- [ ] Token-budget awareness: estimate and report context costs before long tasks

---

## Medium-Term (Phase 5.3)

**Theme: Development Workflow Integration**

- [ ] Git connector: status, diff, stage, commit (approval-gated), branch management
- [ ] Shell/terminal connector: command execution with sandboxed subprocess isolation
- [ ] Python/Node REPL connector: governed code execution with output capture
- [ ] File-watcher integration: trigger agent analysis on file change events
- [ ] Dependency vulnerability scanning (pip-audit / npm audit wrappers)

---

## Medium-Term (Phase 5.4)

**Theme: Application Ecosystem Expansion**

Priority connectors (each requires: discover → inspect → modify → approval chain):

- [ ] VS Code connector (extension API or LSP)
- [ ] Browser connector (headless Chrome via CDP)
- [ ] Obsidian connector (vault file operations, note linking)
- [ ] Blender connector (Python API, scene inspection, render)
- [ ] OBS Studio connector (scene switching, source management)
- [ ] JIRA / GitHub Issues connector (read-only first, write with approval)

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
3. **No global input injection.** No pyautogui, SendInput, clipboard injection, or global keyboard/mouse hooks.
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
| REAPER project editing | ✅ | ✅ | ✅ | 🟡 (needs REAPER installed) |
| Experience learning | ✅ | ✅ | ✅ | 🟡 |
| Multi-step planning | 🟡 | 🟡 | ✅ | 🔴 |
| Parallel task execution | 🔴 | 🔴 | 🟡 | 🔴 |
| Git connector | 🔴 | 🔴 | 🟡 | 🔴 |
| Browser connector | 🔴 | 🔴 | 🟡 | 🔴 |
| Skill library versioning | 🔴 | 🔴 | 🟡 | 🔴 |

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
