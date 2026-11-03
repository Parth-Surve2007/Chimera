# Chimera

**A model-agnostic, local-first general-purpose AI computer agent.**

Chimera is designed to understand and execute complex tasks across your digital environment: general computer and application control, software engineering, authorized cybersecurity research, technical analysis, and governed self-learning. It aims to become an extensible intelligence layer that operates with explicit operator control, local-first by default, and governed execution at every step.

---

## Vision & Philosophy

Chimera is not a chatbot or a coding assistant. It is an agent platform that:

- **Remains model-agnostic** — Switch between local GGUF models, OpenAI, Anthropic, Gemini, Meta, or custom providers without changing your workflow.
- **Prioritizes local-first** — Run inference locally when possible; remote providers are explicit choices, never silent fallbacks.
- **Governs all execution** — Every tool call, application action, and system modification passes through approval gates with full audit trails.
- **Learns from experience** — Capture execution traces, extract reusable skills, and promote them with operator approval.
- **Integrates applications** — Connect to specialized tools (IDEs, browsers, build systems, analysis tools) through a governed Application SDK.
- **Reverse-engineers software** — REA provides binary and artifact analysis as one specialized capability domain.

Chimera does not claim to be a completed AGI or frontier model equivalent. It is a foundation for building capable, transparent, and controllable AI agents.

---

## Key Capabilities

| Capability | Status |
|------------|--------|
| **Multi-provider inference** | ✅ Implemented — 8 provider adapters (OpenAI, Anthropic, Gemini, Meta, llama.cpp, Custom) |
| **Local GGUF inference** | ✅ Implemented — llama.cpp integration with explicit model configuration |
| **AgentRuntime & session persistence** | ✅ Implemented — SQLite-backed sessions, messages, and events |
| **Workspace tools (read/write)** | ✅ Implemented — Governed file operations with approval gates |
| **Computer use (Windows)** | 🟡 Partial — operator-authorized process sessions, fresh UIA references and exact semantic patterns; global keyboard/mouse injection is disabled |
| **Application SDK** | ✅ Implemented — Universal connector framework with risk levels |
| **REA: Reverse Engineer Anything** | ✅ Implemented — Artifact detection, static analysis, Ghidra integration |
| **Ghidra integration** | 🟡 Partial — Installation discovery and bounded static parsing; automated headless analysis actions remain unavailable |
| **Experience learning** | 🟡 Partial — Trace capture, secret redaction, stored skill proposals and approval status updates; replay and promotion remain planned |
| **Task planning** | 🟡 Implemented foundation — dependency-aware sequential templates, AgentRuntime-governed execution, permission checks and approval pause/resume; model-driven planning and parallel execution remain planned |
| **Application connectors** | 🟡 Foundation — Ghidra and REA connectors, ecosystem extensible |

---

## REA: Reverse Engineer Anything

**REA is one specialized capability domain within Chimera.**

REA stands for Reverse Engineer Anything. It is NOT Cockos REAPER (the audio workstation). REA provides Chimera with the ability to understand software artifacts, binaries, firmware, file formats and application internals through multiple analysis backends.

### What REA Does

**Artifact Detection**
- Progressive identification of PE (Windows), ELF (Linux), Mach-O (macOS), .NET, APK, DEX, firmware images, archives, and static libraries
- Format detection from byte signatures and structural headers
- Architecture and endianness identification (x86, x86_64, ARM, AArch64, MIPS, RISC-V)
- Target OS detection where available

**Built-in Static Analysis**
- Zero-dependency Python parsers for PE32, PE32+, ELF32, ELF64
- Hash calculation (SHA-256, MD5, SHA-1)
- ASCII and UTF-16LE string extraction with file offsets
- Section and header inspection with entropy analysis
- Export and import symbol enumeration
- Symbol table parsing (symtab, dynsym)
- Safe bounds checking — malformed files fail gracefully

**Backend Architecture**
- Modular adapters for specialized tools:
  - **Ghidra** — NSA Ghidra SRE Suite (headless analysis, decompilation)
  - **radare2** — radare2 reverse engineering framework (discovery-only, governed)
  - **Rizin** — Rizin binary analysis framework (discovery-only, governed)
- Backend capability manifests declare supported operations
- Engine selects appropriate backend per capability
- Missing backends report clear status rather than fabricating success

**Application SDK Integration**
- REA exposed through `ReaConnector` with governed actions:
  - `analyze_artifact` — Comprehensive static analysis
  - `extract_strings` — String extraction
  - `detect_format` — Format identification
  - `analyze_unknown` — Progressive unknown binary analysis
  - `run_ghidra_headless` — Ghidra headless execution (Level 2, requires approval)
  - `list_backends` — Backend enumeration
- All actions pass through AgentRuntime permissions and approval pipeline

**Security Controls**
- Never automatically executes imported binaries
- Strict file-size limits (150MB default)
- Bounded string scanning (4MB max, 2000 strings)
- Path validation with optional root restrictions
- Read-only analysis by default
- Subprocess timeouts (60 seconds)

### What REA Does Not Do

- REA does not claim complete parsing for every detected format
- Ghidra, radare2, and Rizin require their respective installations for external analysis
- Dynamic analysis capabilities are not yet implemented
- Automatic decompilation requires native Ghidra and JDK 17+

---

## Universal Model-Provider Architecture

Chimera's provider registry supports 8 adapter types through a unified interface:

**Supported Protocols**
- OpenAI-compatible APIs (Azure, DeepSeek, local vLLM, etc.)
- OpenAI Responses API
- Anthropic Messages API
- Gemini Content API
- Gemini Interactions API
- Meta Model API (requires credentials)
- llama.cpp (local GGUF models)
- Custom adapters via SDK

**Features**
- SSRF protection: DNS rebinding protection, 0.0.0.0 rejection, IPv4-mapped IPv6 blocking
- Capability detection via provider compatibility lab
- Usage accounting and token tracking
- Type-validated adapter SDK (`chimera_provider_sdk/`)
- No silent cloud fallback — model selection is always explicit

**Local Inference**
- Configure via `CHIMERA_MODEL_PATH` (GGUF file) and `CHIMERA_LLAMA_CLI` (llama.cpp executable)
- Optional: `CHIMERA_MODEL_CONTEXT`, `CHIMERA_MODEL_MAX_TOKENS`, `CHIMERA_MODEL_TIMEOUT`, `CHIMERA_MODEL_THREADS`, `CHIMERA_MODEL_GPU_LAYERS`
- Model weights are never downloaded automatically
- Run `python -m chimera.smoke_test` to verify local inference without fake success

---

## Application Integration Ecosystem

The Application SDK provides a universal framework for connecting Chimera to external software.

**Connector Architecture**
- `BaseApplicationConnector` — Abstract base for all integrations
- `ApplicationManifest` — Declares capabilities, risk levels, and permissions
- `ApplicationAction` — Individual operations with argument schemas
- `ActionRiskLevel` — Five-tier risk taxonomy (Observe → Recommend → Reversible → Protected → External Sensitive)
- `ApplicationRegistry` — Central discovery and execution dispatcher

**Current Connectors**
- **GhidraConnector** — Headless binary analysis, PE/ELF string extraction, function enumeration
- **ReaConnector** — REA reverse-engineering intelligence through Application SDK

**Governance**
- Plan Mode blocks Level 2+ actions (read-only execution)
- Build Mode requires approval for Level 3+ actions
- Read-only permissions (`ghidra.read`, `rea.read`) do not require approval in Build Mode
- All actions are logged with full provenance

**Extensibility**
- Implement `BaseApplicationConnector` with `discover()` and `execute_action()`
- Register via `ApplicationRegistry.register(MyConnector())`
- Actions exposed to agents through normalized tool catalog

---

## AgentRuntime & Security Architecture

**Execution Authority**
AgentRuntime is the sole execution authority. Model adapters do not directly execute tools. All tool calls follow this pipeline:

```
User Request
    ↓
Chimera Interface
    ↓
AgentRuntime (Session Store, Task Policy)
    ↓
Planning & Task Orchestration
    ↓
Universal Provider Interface
    ↓
Model Provider (adapter selected)
    ↓
Normalized Tool Calls
    ↓
Permission & Approval System
    ↓
Application Registry / Tool Catalog
    ↓
Application Connectors & REA
    ↓
Execution Results (with evidence)
    ↓
Persistent Memory & Audit Trail
```

**Security Model**
- No silent cloud fallback — provider selection is explicit
- No capability escalation without approval — write/execute actions require authorization
- No global input injection — no pyautogui, SendInput, or global keyboard/mouse hooks
- No automatic commits or pushes — VCS operations require explicit confirmation
- No model downloads without consent — operator must authorize acquisition
- Audit trail always on — every tool invocation, approval, and provider call is logged
- SSRF protection always active — provider endpoint validation enforced
- Local-first by default — remote inference only when operator chooses

**Session Persistence**
- SQLite-backed sessions, messages, and events
- Resume interrupted conversations
- Full replay capability for audit and debugging

---

## Memory & Governed Self-Learning

**ExperienceLearningEngine**
- Captures execution traces from agent sessions
- Inspects tool calls, arguments, and results
- Redacts secrets and sensitive data automatically
- Extracts reusable workflows as `ProposedSkill` objects

**Skill Lifecycle**
1. **Proposed** — Extracted from a trace and stored for operator review
2. **Approved** — Operator approval is recorded in the skill store

Approved proposals are not yet installed or automatically replayed as executable skills. Rejection, versioning, regression testing and governed replay remain planned.

**Current State**
- Trace capture and secret redaction: ✅ Implemented
- Skill extraction and storage: ✅ Implemented
- Approval status update: ✅ Implemented
- Rejection, versioning, regression testing and executable replay: 🟡 Planned
- Automated regression testing: 🟡 Planned

---

## Architecture Diagram

```mermaid
graph TD
    User[User] --> Interface[Chimera Interface]
    Interface --> Runtime[AgentRuntime]
    Runtime --> Planning[Planning & Task Orchestration]
    Runtime --> SessionStore[Session Store]
    Runtime --> TaskPolicy[Task Policy]

    Planning --> ProviderInterface[Universal Provider Interface]
    ProviderInterface --> ProviderRegistry[Provider Registry]
    ProviderRegistry --> ModelProvider[Model Provider]

    Runtime --> ToolCatalog[Tool Catalog]
    ToolCatalog --> PermissionSystem[Permission & Approval System]
    PermissionSystem --> AppRegistry[Application Registry]

    AppRegistry --> Connectors[Application Connectors]
    AppRegistry --> REA[REA: Reverse Engineer Anything]

    Connectors --> Execution[Execution Results]
    REA --> Execution

    Execution --> Memory[Persistent Memory]
    Execution --> Audit[Audit Trail]

    Memory --> Runtime
    Audit --> Runtime

    ModelProvider --> Planning
```

---

## Technology Stack

**Core**
- Python 3.11+
- Flet 1.0+ (native desktop UI)
- SQLite (session persistence, registry, audit)

**Provider Adapters**
- `httpx` (HTTP client with timeout limits)
- `pydantic` (request/response validation)
- Type-validated adapter SDK

**Application SDK**
- Universal connector framework
- Risk-based action governance
- Permission and approval pipeline

**REA**
- Zero-dependency PE/ELF parsers
- Shannon entropy calculation
- Modular backend architecture

**Learning**
- Trace capture and inspection
- Secret redaction via regex patterns
- JSON-based skill storage

**Computer Use**
- pywinauto (Windows UI automation, governed)
- Subprocess supervision with timeouts

---

## Installation & Setup

### Requirements
- Python 3.11 or later
- Windows (primary platform), Linux/macOS support varies by feature

### Installation from Source

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

### Local Model Configuration (Optional)

Set environment variables or configure in Settings:

```powershell
$env:CHIMERA_MODEL_PATH = "path\to\model.gguf"
$env:CHIMERA_LLAMA_CLI = "path\to\llama-cli.exe"
```

Optional limits:
- `CHIMERA_MODEL_CONTEXT` — Context window size
- `CHIMERA_MODEL_MAX_TOKENS` — Maximum generation tokens
- `CHIMERA_MODEL_TIMEOUT` — Inference timeout (seconds)
- `CHIMERA_MODEL_THREADS` — Thread count
- `CHIMERA_MODEL_GPU_LAYERS` — GPU offload layers (0 for CPU)

### Launch

```powershell
python -m chimera
```

Or use the entry point:

```powershell
chimera
```

### Data Directory

- Source runs: `.chimera/` (relative to working directory)
- Packaged builds: `%LOCALAPPDATA%\Chimera`
- Override: Set `CHIMERA_DATA_DIR` environment variable

### Windows Build (Optional)

```powershell
python -m pip install pyinstaller
powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1
```

Launch packaged build:

```powershell
scripts\run_chimera.bat
```

---

## Configuration & Model Providers

### Configuring External Providers

Set credentials via environment variables or configure in Settings:

**OpenAI**
```powershell
$env:OPENAI_API_KEY = "sk-..."
```

**Anthropic**
```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-..."
```

**Gemini**
```powershell
$env:GOOGLE_API_KEY = "..."
```

**Meta**
```powershell
$env:META_API_KEY = "..."
```

### Provider Selection

Models are selected explicitly through the UI or configuration. Chimera never silently switches providers. The provider registry validates capabilities before use.

---

## Usage & Examples

### Basic Conversation

Launch Chimera and start a conversation. The AgentRuntime manages sessions, messages, and tool execution.

### Reverse-Engineering Analysis

```
User: Analyze this executable
Chimera: [Invokes REA connector] Analyzing artifact... Format: PE32, Architecture: x86_64, Exports: 12, Imports: 5, Entropy: 6.2/8.0
```

### Application Integration

```
User: Extract strings from this binary
Chimera: [Invokes REA] Extracted 24 strings (min length 4): "version", "copyright", "OpenSSL", ...
```

### Computer Use (Windows)

```
User: Open Notepad and type "Hello"
Chimera: Lists visible applications (read-only). The operator selects and authorizes one process in APPLICATIONS. Chimera inspects its UI Automation tree. Protected Invoke/Value/Toggle/Selection/Expand actions require approval and target the exact observed control. If the required semantic pattern is unavailable, Chimera stops for manual operator input.
```

---

## Security & Privacy

**Local-First Philosophy**
- Local inference is preferred where possible
- Remote providers are explicit choices, never automatic
- All data storage is local (SQLite, JSON)

**Audit Trail**
- Every tool invocation is logged with timestamp, arguments, and result
- Approval decisions are recorded with operator identity
- Provider calls are tracked with provenance

**Data Handling**
- Secrets are redacted from learning traces
- File paths are validated against configured workspaces
- Size limits prevent unbounded resource consumption

**Network Security**
- SSRF protection prevents DNS rebinding attacks
- 0.0.0.0 and IPv4-mapped IPv6 addresses are rejected
- Provider endpoints are validated before outbound requests

---

## Current Limitations

**Architecture**
- Multi-step planning is foundational, not yet dependency-graph complete
- Parallel task execution is not implemented
- Skill library versioning and regression testing are planned

**Application Ecosystem**
- Ghidra connector requires native Ghidra and JDK 17+ for headless execution
- radare2 and Rizin backends are discovered but not auto-executed (governed mode)
- Only Ghidra and REA connectors are implemented; broader ecosystem is planned

**Computer Use**
- Windows UI Automation is partial and requires explicit per-conversation application authorization
- Only exact UIA semantic patterns are supported for mutations; global keyboard/mouse injection, foreground stealing and remote window closing are disabled
- No Linux/macOS computer use implementation
- No global input injection (intentional security choice)

**Learning**
- Skills are stored but not yet versioned or automatically tested
- Skill federation/sharing is planned

**Platform**
- Windows is the primary platform; Linux/macOS support varies by feature

---

## Roadmap

See [ROADMAP.md](ROADMAP.md) for detailed phase planning.

**Current Phase: 5.3 — Cross-Capability Agentic Workflows**

**Implemented foundation (Phase 5.3)**
- Dependency-aware sequential workflow templates for workspace and REA tasks
- Runtime-owned dispatch for every workflow step
- Per-step permission validation and protected-action approval pause/resume
- Persisted step results and verification states
- Fail-closed handling for unresolved dependencies and ambiguous artifact discovery

**Next (Phase 5.4)**
- Model-driven plan generation and replanning
- Safe resume of interrupted workflow runs
- Dependency graph visualization
- Bounded parallel execution for read-only steps
- Token-budget awareness and sub-task delegation

**Medium-Term (Phase 5.5)**
- Git connector (status, diff, stage, commit)
- Shell/terminal connector with sandboxed isolation
- Python/Node REPL connector
- File-watcher integration
- Dependency vulnerability scanning

**Medium-Term (Phase 5.6)**
- VS Code connector
- Browser connector (headless Chrome)
- Obsidian connector
- Blender connector
- OBS Studio connector
- JIRA / GitHub Issues connector

**Long-Term (Phase 6.0)**
- Skill library with versioning and rollback
- Automated regression testing of promoted skills
- Operator-defined capability profiles
- Natural-language policy engine
- Federated skill sharing
- Multi-modal inputs (screen capture, audio, images)

---

## Contribution Guidelines

Chimera welcomes contributions that align with its local-first, governed execution philosophy.

**Contributing a Provider Adapter**

See [`docs/provider_integration_guide.md`](docs/provider_integration_guide.md) for the adapter SDK.

**Contributing an Application Connector**

Implement `BaseApplicationConnector` from `chimera/applications/base.py`:

```python
from chimera.applications.base import BaseApplicationConnector
from chimera.applications.manifest import ApplicationManifest, ApplicationAction, ActionRiskLevel

class MyAppConnector(BaseApplicationConnector):
    def discover(self) -> ApplicationManifest:
        # Probe installation, return manifest
        pass

    def execute_action(self, action_id, arguments, approved=False) -> dict:
        # Execute governed action
        pass
```

Register via `ApplicationRegistry.register(MyAppConnector())`.

**Contributing to REA**

Extend backend adapters in `chimera/reverse_engineering/backends/` or enhance static parsers in `builtin_static.py`.

**Testing**

Run tests with:

```powershell
python -m pytest
```

**Governance Principles**

All contributions must respect:
- No silent cloud fallback
- No capability escalation without approval
- No global input injection
- No automatic commits or pushes
- No model downloads without consent
- Audit trail always on
- SSRF protection always active
- Local-first by default

---

**Chimera is a local-first platform. This README reflects what can be built without cloud dependencies, proprietary licenses, or surveillance infrastructure.**
