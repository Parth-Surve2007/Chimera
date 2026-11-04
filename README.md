# 🐉 Chimera

### Your desktop. Your tools. Your rules.

**Chimera is a local-first AI computer-workbench for turning natural-language intent into governed, observable digital work.** It combines a persistent agent runtime, selectable language models, Windows desktop control, application connectors, local file and document tools, browser automation, and evidence-aware workflows.

Chimera is designed to do more than produce instructions. It can inspect a workspace, plan a task, invoke registered tools, request approval before protected changes, execute supported actions, inspect the result, and preserve the work trail in a local session.

> **The central idea:** intelligence proposes, policy authorizes, tools act, and evidence decides whether the work actually happened.

<p align="left">
  <img alt="Python 3.11+" src="https://img.shields.io/badge/Python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white">
  <img alt="Windows desktop" src="https://img.shields.io/badge/Desktop-Windows-0078D4?style=flat-square&logo=windows&logoColor=white">
  <img alt="Local first" src="https://img.shields.io/badge/Architecture-Local--first-BC8B3C?style=flat-square">
  <img alt="Flet" src="https://img.shields.io/badge/UI-Flet-222222?style=flat-square">
  <img alt="llama.cpp" src="https://img.shields.io/badge/Inference-llama.cpp-6D8B74?style=flat-square">
</p>

---

## 🧠 The Chimera approach

Most assistants stop at an answer. Chimera is built around a work loop:

1. **Understand** the request and the active workspace.
2. **Plan** the work and identify the tools and permissions it needs.
3. **Authorize** protected operations through explicit, action-specific approval.
4. **Execute** through registered capabilities rather than arbitrary model-generated code.
5. **Verify** outputs and inspect post-action state where a verifier is available.
6. **Report** what succeeded, what failed, and what evidence supports the result.

A model saying “done” is not proof that a file exists, a button was activated, or an application changed state. Chimera stores tool outcomes and verification evidence separately from the model's narration.

### The systems working together

| System | What it contributes |
|---|---|
| **AgentRuntime** | Persistent conversations, model decisions, registered tool dispatch, approval pauses, execution feedback, and durable events. |
| **Model Bay** | Local GGUF configuration, persistent llama.cpp server sessions, explicit provider profiles, and visible model selection. |
| **Computer Runtime** | Windows process discovery, per-conversation process authorization, fresh UI Automation observations, and scoped interaction. |
| **Application SDK** | Manifest-backed connectors, declared actions, argument contracts, risk levels, permissions, and availability discovery. |
| **Workspace Tools** | Bounded local file discovery, inspection, search, reading, and approval-gated artifact creation. |
| **Workflow Engine** | Multi-step execution, dependency-aware planning, policy checks, persisted run state, and evidence-aware outcomes. |
| **Goal Ledger** | Durable operator-defined goals, priorities, acceptance criteria, and supporting evidence. |
| **Experience Learning** | Trace review, secret redaction, reusable skill proposals, and operator-controlled approval. |

---

## ✨ Capabilities

### Persistent agent conversations

Chimera's desktop workbench is backed by a persistent runtime rather than a disposable prompt window.

- Conversations and messages are stored locally in SQLite.
- Session events are sequenced and persisted for replayable activity history.
- The active conversation retains its selected model and Plan or Build mode.
- Tool calls are structured invocations with arguments and results.
- Pending protected actions can pause for an operator decision and resume through the runtime.
- Failed tool results can be returned to the model as observations, allowing a bounded correction attempt.
- Repeated identical failed calls are guarded against rather than retried indefinitely.

### Model choice and local inference

Chimera is local-first, not local-only.

- Run local GGUF models through a configured llama.cpp llama-server.
- Use an explicitly configured llama-cli path when one-shot inference is preferred.
- Persist model profiles, runtime settings, context budgets, and the selected provider.
- Use native GGUF chat templates and structured decision output when supported by the selected model and runtime.
- Configure compatible external API profiles when desired.
- Select hosted providers explicitly. A failed local model does not silently switch to another model or a hosted service.
- Keep model files and inference executables under operator control. Chimera does not silently download models.

A separate opt-in local vision provider can inspect screenshots using a configured multimodal GGUF and llama.cpp server. It is disabled unless its local model configuration is supplied. Visual perception is evidence for understanding a screen, not permission to interact with it.

### Windows desktop control

Chimera can discover visible Windows application windows at runtime and work with supported controls through Windows UI Automation.

- Live window and process discovery.
- Exact process identity checks and per-conversation authorization.
- Fresh UI Automation snapshots before supported interactions.
- Reading accessible names, text patterns, values, and bounded table/grid information.
- Semantic actions such as invoke, set value, toggle, select, expand, and collapse.
- Bounded scrolling and supported item navigation.
- Foreground-scoped pointer and keyboard interaction when a supported semantic control is not available.
- Optional Cua Driver transport for snapshot-bound background interactions where the backend is available.
- Canvas drawing uses background drag delivery first. A foreground retry is exposed only after the driver explicitly recommends it for that exact drawing attempt, and it requires a fresh canvas snapshot plus a separate higher-risk operator approval.
- Post-action observation so an accepted input event is not confused with a verified task result.

The runtime does not silently escalate a background action into foreground input. A process being visible or installed does not automatically authorize it for the current conversation.

### Browser automation

The browser connector uses an isolated visible Chromium session through Playwright. It is designed for controlled browser work without reusing the operator's everyday browser profile.

Supported browser operations include inspecting the current page, navigating, locating unique controls, interacting with supported elements, filling ordinary text fields, selecting dropdown values, uploading approved workspace files, capturing screenshots, and downloading files.

Browser mutations are subject to the runtime's approval policy. Page content is untrusted input, and password fields are not filled by the agent.

### Local files and document creation

Chimera includes bounded workspace tools for practical file work.

- List files and inspect metadata.
- Search filenames and supported text content.
- Read text files within configured size limits.
- Create new text and source files.
- Generate genuine Word DOCX documents.
- Generate PDF documents and reopen them to verify extracted text and page structure.
- Preserve existing files by refusing to overwrite a destination through the built-in create operations.

On Windows, the default workspace scope is the system drive. The tools run with the permissions of the Windows account that launched Chimera. They do not elevate privileges or bypass filesystem ACLs.

### Application integrations

Chimera uses a manifest-backed application connector SDK. A connector declares its identity, integration surfaces, supported actions, argument schema, risk level, permissions, preconditions, and known limitations.

| Integration | What it does | External requirement |
|---|---|---|
| **Browser** | Isolated browser inspection and controlled web interaction. | Edge or Chrome executable and Playwright runtime. |
| **Blender** | Validated primitive-based scene creation, saved BLEND project, and PNG preview. | Blender installed locally. |
| **Ghidra** | Installation discovery and bounded binary inspection, including supported string and function analysis. | Ghidra and its required Java runtime for Ghidra-backed operations. |
| **REA** | Built-in static artifact identification and inspection for supported binary formats. | No separate application required for built-in analysis. |

Connector availability is discovered at runtime. A connector being present in the repository does not imply that its companion application is installed or that every declared action is available.

#### Blender scene generation

The Blender connector accepts a bounded JSON scene recipe, validates supported primitives and values, requests approval, and invokes Blender with a fixed script. It can create simple scenes with mesh primitives, transforms, basic materials, a camera, lighting, a saved project, and a rendered PNG preview.

It does not execute model-supplied Python scripts and is not presented as a general-purpose 3D modelling interface.

#### Ghidra and REA

REA provides built-in static inspection for supported artifacts, including format detection, structural metadata, and string extraction. The Ghidra connector adds discovery and bounded Ghidra-related analysis when the external application and runtime are available.

The connector reports unavailable actions honestly. The presence of a Ghidra installation is not treated as proof that arbitrary Ghidra scripts can be executed.

### Multi-step workflows and task governance

Chimera can represent work as structured tasks and sequential workflows rather than relying on an unstructured chain of model messages.

- Workflows can express dependencies between steps.
- Each action is checked against the active policy.
- Protected operations pause for approval.
- Runs and step outcomes are persisted.
- Failures and blocked actions remain visible in the run history.
- Evidence-aware evaluation distinguishes pass, fail, unknown, and blocked.
- An inconclusive check is not promoted to a successful result.
- Proposal acquisition and verification use bounded attempts and staging rather than treating an unverified candidate as accepted.

### Goals, memory, and reusable skills

Chimera separates durable goals from individual conversations and task attempts.

- Create operator-owned goals with priorities, descriptions, tags, and success criteria.
- Attach evidence to goals as work progresses.
- Keep completion as an explicit state transition rather than inferring it from a model response.
- Capture execution traces for review.
- Redact detected secrets before preparing reusable skill proposals.
- Store proposed skills with declared permissions, parameters, expected outputs, and reusable steps.
- Require operator approval before a proposed skill is treated as approved.

This is governed experience capture, not unrestricted self-modification. A trace does not automatically become executable authority.

---

## 🧭 The desktop workbench

The Flet desktop interface organizes the main systems into focused work areas:

| Area | Purpose |
|---|---|
| **Chat** | Continuous conversation, task composition, tool activity, and Plan/Build controls. |
| **Model Bay** | Local model selection, runtime configuration, and provider profiles. |
| **Applications** | Installed and running application discovery and connector availability. |
| **Workflow Studio** | Structured multi-step workflow authoring and execution. |
| **Capabilities** | First-party capabilities and custom capability proposals. |
| **Goals** | Durable goals, priorities, criteria, and evidence. |
| **Run History** | Persisted workflow and execution activity. |

The workbench uses a dark graphite and neutral visual system. The interface exposes the active mode and approval boundary so the operator can distinguish planning from protected execution.

---

## 🔐 Safety and execution model

Chimera is designed around explicit authority boundaries.

**Plan Mode** is for reasoning, inspection, and preparation. Protected actions are blocked. The model can identify the tools a task would need without receiving permission to perform protected changes.

**Build Mode** enables governed execution. Actions that modify files, application state, or external resources are subject to their declared risk level and permission policy. When approval is required, Chimera presents the specific action and its arguments for an operator decision.

### Evidence and verification

- Tool arguments are validated against registered action contracts.
- Application actions are checked against their connector manifests.
- Windows interactions are tied to an authorized process and fresh observation.
- Filesystem operations have explicit scope and size limits.
- Created documents are checked after writing.
- Workflow evaluation does not treat missing evidence as a pass.
- The final response should distinguish completed work from attempted, blocked, failed, or unverified work.

### Local-first privacy

- Local inference is available without sending prompts to a hosted model provider.
- Hosted model profiles are opt-in and explicitly selected.
- Local model and vision files are supplied by the operator.
- Browser automation uses an isolated profile rather than the user's default browser profile.
- Application and filesystem operations run under the current user's operating-system permissions.

---

## 🗂️ Filesystem scope and limits

On Windows, the default local workspace is the system drive, typically:

    C:\

The workspace tools can resolve known folders such as the current user's Desktop relative to that root. There is no separate project-attachment step required for the default drive scope.

Discovery is deliberately bounded:

- Directory traversal skips inaccessible folders and does not follow symbolic links or Windows junctions.
- A listing returns at most 200 files.
- Filename search examines at most 2,000 files and returns at most 200 matches.
- Text search scans at most 2,000 files and 50 MB of candidate text.
- Individual text reads and new-file contents are limited to 2 MB.
- New text/source files must use a supported extension.
- Word documents and PDFs use dedicated document operations.
- Built-in create operations do not overwrite existing files.

These limits are intentional. Broad workspace access is not unrestricted system access, and protected Windows or application files may remain inaccessible.

---

## 🪟 Windows application-control flow

A typical governed desktop interaction follows this sequence:

1. Refresh the live application inventory.
2. Select an installed application to launch with approval, or authorize an already-running process.
3. Verify the process identity and bind it to the current conversation.
4. Capture a fresh UI Automation observation.
5. Choose a supported semantic action or bounded scoped input.
6. Apply the action only after the required approval.
7. Capture a new observation and report what changed.

Chimera supports the control patterns exposed by the target application. Some desktop software exposes incomplete or no useful UI Automation information. In those cases, a native connector or manual handoff may be needed.

---

## ⚙️ Install on Windows

### Requirements

- Windows 10 or Windows 11, 64-bit.
- Python 3.11 or newer for a source checkout.
- Internet access during initial dependency installation.
- WinGet for optional companion-app setup.
- A local GGUF model and matching llama.cpp executable for local inference.

### 1. Clone the repository

    git clone https://github.com/Parth-Surve2007/Chimera.git
    cd Chimera

### 2. Run the Windows setup helper

    scripts\setup_windows.bat

The helper installs Chimera and its test/packaging dependencies into the active global Python user installation. **It does not create a virtual environment.**

When WinGet is available, the helper offers to install Blender through BlenderFoundation.Blender, Eclipse Temurin JDK 25 through EclipseAdoptium.Temurin.25.JDK, and the latest official Ghidra public ZIP release through scripts\install_ghidra.ps1.

The setup process is interactive. Review installation prompts and Windows permission dialogs. It does not download a GGUF model. If WinGet is unavailable, install Blender, Temurin, and Ghidra manually.

### 3. Launch Chimera

    python -m chimera

The first launch creates the local Chimera data directory. Source runs use .chimera\ in the repository unless CHIMERA_DATA_DIR is set. Packaged runs use the current user's LocalAppData directory.

### 4. Configure a local model

1. Open **Model Bay**.
2. Select your GGUF model.
3. Select the matching llama-server.exe for persistent inference, or explicitly configure llama-cli.exe for one-shot mode.
4. Start with **APPLY LOW-MEMORY PRESET**: 4K context, 384 output tokens, 4 CPU threads, and adaptive GPU offload. This is the safer starting point for 16 GB RAM systems. Use **APPLY HIGH-COMPUTE PRESET** only when the machine has enough free memory and the larger context materially helps your tasks.
5. Save the configuration and load the model. The llama-server must restart to apply changed settings. Existing saved user budgets are preserved; changing presets is an explicit operator choice.

Persistent llama-server inference uses the GGUF's native chat template and schema-constrained JSON decisions when supported by the selected model and runtime. Failed actions are fed back as role-separated observations, and exact repeated failed tool calls are stopped. Runtime structure can improve execution discipline, but it cannot make a small model reason beyond its underlying capabilities.

Chimera does not silently change providers, download a model, or fall back to a hosted service when a local runtime fails. Hosted API profiles can be added separately and are used only when explicitly selected for the conversation.

### Optional local vision

Configure the local vision provider with environment variables before launching Chimera:

    CHIMERA_VISION_MODEL_PATH=C:\Models\vision-model.gguf
    CHIMERA_VISION_SERVER=C:\llama.cpp\llama-server.exe
    CHIMERA_VISION_MMPROJ_PATH=C:\Models\mmproj.gguf

The multimodal model and matching projector file must be compatible with the selected llama.cpp build. Vision is opt-in and does not grant desktop interaction permissions.

---

## 📦 Build a portable Windows application

The repository includes a PyInstaller build script. From the repository root, run this in PowerShell or Command Prompt:

    powershell -NoProfile -ExecutionPolicy Bypass -File ".\scripts\build_windows.ps1"

The script runs the configured tests and compile checks, prepares the Flet desktop client, creates the frozen application, and writes:

    dist\
    ├── Chimera\
    │   └── Chimera.exe
    └── Chimera-Windows-portable.zip

Use -SkipTests only when you have already run the test suite and intentionally want to skip it during packaging:

    powershell -NoProfile -ExecutionPolicy Bypass -File ".\scripts\build_windows.ps1" -SkipTests

The portable application bundles its Python runtime and Flet desktop client. GGUF model files, llama.cpp executables, Blender, and Ghidra remain separate local installations.

---

## 🧪 Development

Install the source and test dependencies with:

    python -m pip install --user -e ".[test]"

To enable the optional Cua Driver background desktop backend in a source run:

    python -m pip install --user -e ".[computer-use]"

The Windows portable build includes this optional SDK automatically. Source installs without it retain the UIA/Win32 desktop backends.

Run the CI test selection:

    python -m pytest tests/ --ignore=tests/test_ui.py -q

Run the full local test suite when working on the UI:

    python -m pytest -q

Compile-check the application and tests:

    python -m compileall -q chimera tests

### Repository map

    chimera/
    ├── agent_runtime.py        # Persistent sessions, tool policy and execution loop
    ├── local_model.py          # Local llama.cpp reasoning and prompt construction
    ├── model_hub.py            # Model profiles and explicit provider selection
    ├── tools.py                # Bounded workspace tools
    ├── documents.py            # Word document generation
    ├── pdf_documents.py        # PDF generation and verification
    ├── builtin_capabilities.py # First-party capability inventory
    ├── computer_use.py         # Windows desktop discovery and interaction
    ├── computer_runtime.py     # Process authorization and UIA evidence
    ├── cua_backend.py          # Optional Cua Driver transport
    ├── scoped_input.py         # Approved scoped pointer and keyboard input
    ├── applications/           # Connector SDK, registry and integrations
    ├── reverse_engineering/    # REA static analysis engine
    ├── learning.py             # Trace review and governed skill proposals
    ├── goals.py                # Operator-owned goals and evidence
    ├── workflow.py             # Governed workflow execution
    ├── evaluation.py           # Evidence-aware acceptance evaluation
    └── ui.py                   # Flet desktop workbench

    scripts/
    ├── setup_windows.bat       # Dependencies and optional companion setup
    ├── install_ghidra.ps1      # Official Ghidra release installer
    └── build_windows.ps1       # Tests, Flet client prep and portable package

### Runtime architecture

    Desktop Workbench
          |
          v
      AgentRuntime
          |
       TaskPolicy
          |
       +--+-------------------+
       |          |           |
       v          v           v
    Workspace  Authorized  Specialist
      tools    Windows apps connectors
       |          |           |
       +----------+-----------+
                  |
                  v
           Persisted evidence
           SQLite · Runs · Goals

---

## 🧱 Compatibility boundaries

Chimera is an extensible computer-work platform, not a promise that every third-party application can already be automated in every situation.

- Windows desktop automation is platform-specific and depends on the target app exposing useful UI Automation controls.
- A visible application is not automatically authorized. Authorization is per conversation and exact process identity.
- Some applications expose no semantic controls or expose only a partial UIA tree.
- Blender generation targets validated primitive-based scenes rather than unrestricted modelling.
- Ghidra-backed operations depend on the external installation and required Java runtime. Direct arbitrary Ghidra script execution is not exposed as an available connector action.
- Browser automation requires an installed Edge or Chrome executable and a working Playwright runtime. Chimera does not download browser engines or reuse the user's default browser profile.
- Browser page content is untrusted; password fields are not filled by the agent.
- Universal control of arbitrary third-party applications is not guaranteed. Applications with incomplete UI Automation support may need a dedicated native connector or manual handoff.
- Local model quality, context limits, GPU memory, and inference speed depend on the selected GGUF and llama.cpp build.
- Local vision requires a compatible multimodal model and projector configuration. It is not enabled by default.

These boundaries are part of the design: Chimera should report an unavailable capability instead of pretending it succeeded.

---

## 🤝 Contributing

Issues, focused pull requests, test coverage, connector improvements, and documentation fixes are welcome.

A useful contribution should include:

1. A clear problem statement and expected behaviour.
2. Tests for new tool, policy, or persistence behaviour.
3. Honest capability and failure reporting.
4. No hidden hosted fallback, silent downloads, or unapproved protected changes.

For application integrations, prefer a small manifest-backed connector with explicit permissions, argument validation, availability reporting, and tests.

---

## 🔗 Useful links

- [Repository](https://github.com/Parth-Surve2007/Chimera)
- [Issues](https://github.com/Parth-Surve2007/Chimera/issues)
- [Blender downloads](https://www.blender.org/download/)
- [Ghidra releases](https://github.com/NationalSecurityAgency/ghidra/releases)
- [Ghidra installation guide](https://github.com/NationalSecurityAgency/ghidra/blob/master/GhidraDocs/GettingStarted.md)
- [Eclipse Temurin installation](https://adoptium.net/installation/)
- [WinGet documentation](https://learn.microsoft.com/windows/package-manager/winget/)

---

<p align="center">
  <strong>Chimera</strong><br>
  <sub>Local intelligence. Visible permissions. Work that leaves evidence.</sub>
</p>
