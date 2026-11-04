# 🐉 CHIMERA

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/chimera_logo_white.png">
    <img src="assets/chimera_logo_black.png" alt="Chimera" width="660">
  </picture>
</p>

<h3 align="center">One local-first AI. Your whole digital workspace.</h3>
<p align="center">
  <strong>Think beyond chat. Delegate the work.</strong><br>
  From documents and spreadsheets to 3D scenes, code, browser workflows and desktop applications, Chimera is being built to turn natural-language intent into real, reviewable computer work.
</p>

<p align="center">
  <img alt="Python 3.11+" src="https://img.shields.io/badge/Python-3.11%2B-3776AB?style=for-the-badge&logo=python&logoColor=white">
  <img alt="Local first" src="https://img.shields.io/badge/AI-LOCAL--FIRST-9B7653?style=for-the-badge">
  <img alt="Windows" src="https://img.shields.io/badge/Desktop-Windows-0078D4?style=for-the-badge&logo=windows&logoColor=white">
  <img alt="Open source" src="https://img.shields.io/badge/Project-Open%20Source-6D8B74?style=for-the-badge">
</p>

<p align="center">
  <em>Your machine. Your models. Your permissions. Your agent.</em>
</p>

---

## Meet Chimera

Most AI assistants live inside a chat box. **Chimera is being built to work beyond one.**

Chimera is a local-first AI computer-workbench that brings together local language models, persistent agent reasoning, desktop control, application connectors, browser automation, document creation, workflow orchestration and evidence-aware verification.

Give it an outcome, not a twenty-step instruction manual. Chimera's architecture is designed to understand the goal, break it into work, choose from registered capabilities, request permission when an action matters, operate supported software, and check what actually happened.

The ambition is simple and unapologetic:

> **One agent that can move between the tools you already use, get useful work done, and show its receipts.**

Now imagine giving one prompt and moving between **Autodesk Fusion 360, 3ds Max, Maya, Blender, AutoCAD, Cadence, Proteus, Vivado, Word, Excel, PowerPoint, DaVinci Resolve, Photoshop, Illustrator, Premiere Pro, After Effects, VS Code, MATLAB, KiCad** and the rest of your digital toolbox. Build, calculate, design, edit, document, inspect, export. **One conversation. One coordinated workflow.** That is the scale of the Chimera ambition: not another app-specific copilot, but one agent designed to work across the apps where real work happens.

It is not tied to one model, one vendor, one application or one workflow. Run it with local GGUF models, connect an explicitly selected external provider when you choose, and grow its capabilities through an extensible connector system.

<p align="center">
  <img src="assets/chimera_intro.gif" alt="Chimera project introduction" width="820">
</p>

---

## The idea: from a prompt to completed work

Chimera combines the pieces that usually live in separate products:

| Layer | What Chimera brings together |
|---|---|
| **Intelligence** | Local LLMs, optional external model providers, structured decisions and persistent conversation context. |
| **Computer control** | Live application discovery, process-aware authorization, UI Automation, scoped keyboard and pointer input, and optional CUA transport. |
| **Application ecosystem** | Manifest-backed connectors and specialist workflows for applications and services. |
| **Creation** | Text files, Word documents, PDFs, browser downloads, Blender scenes and other bounded artifacts. |
| **Orchestration** | Plans, task checklists, multi-step workflows, dependencies, approvals and persisted run history. |
| **Verification** | Fresh observations, artifact inspection, independent checks and evidence-aware completion states. |
| **Governance** | Local-first execution, explicit permissions, operator-controlled skills and no silent hosted fallback. |

The work loop:

1. **Understand** the outcome and current context.
2. **Plan** the steps, dependencies and capabilities.
3. **Ask** before protected or state-changing actions.
4. **Act** through registered tools and application integrations.
5. **Inspect** the actual application or artifact state.
6. **Verify** the result and keep evidence.
7. **Report** what completed, what did not, and what remains.

A model response saying “done” is not the finish line. The file, application state or workflow result is.

---

## A desktop-wide ambition

Chimera is not meant to be another single-purpose AI for one application. Its target is the software ecosystem people already work in: office suites, creative tools, development environments, browsers, engineering software and 3D production.

### The kinds of applications Chimera is designed to work across

| Workspace | Examples |
|---|---|
| **Documents & office** | Microsoft Word, Excel, PowerPoint, Outlook, OneNote, LibreOffice Writer, Calc and Impress. |
| **Creative & video** | Adobe Photoshop, Illustrator, Premiere Pro, After Effects, DaVinci Resolve, Audacity, OBS Studio, GIMP and Krita. |
| **3D, CAD & design** | Blender, Autodesk Maya, 3ds Max, AutoCAD, Fusion, Inventor, Revit, SolidWorks, FreeCAD and KiCad. |
| **Development** | VS Code, Visual Studio, PyCharm, IntelliJ IDEA, Android Studio, GitHub Desktop, terminals and local development tools. |
| **Research & data** | Jupyter, MATLAB, spreadsheets, databases, technical viewers and analysis tools. |
| **Communication & productivity** | Microsoft Teams, Slack, Discord, Notion, Obsidian and supported web applications. |
| **Browsers & the web** | Microsoft Edge, Google Chrome, Firefox and isolated browser workflows. |

**This is the ecosystem Chimera is aiming at, not a claim that every application above already has a complete dedicated connector.** Today, the actual control surface depends on the integration available: a native connector, browser automation, Windows UI Automation or another supported interaction route. Some apps expose excellent controls; others need deeper, application-specific adapters.

The point of the architecture is that adding a new application should expand the same agent, not create another disconnected assistant.

---

## What is in the repository today

Chimera already has a growing set of working foundations and specialist integrations.

### Local-first model runtime

- Run local GGUF models through a configured llama.cpp `llama-server`.
- Configure `llama-cli` for one-shot inference when preferred.
- Save model profiles, runtime settings and conversation-level model selection.
- Use structured JSON decisions and native model chat templates when supported.
- Configure compatible external API providers explicitly.
- No automatic hosted fallback when a local model fails.
- No silent model downloads.

Chimera is **local-first, not local-only**. You decide where inference happens.

### Persistent agent runtime

- Conversations, messages and events stored locally in SQLite.
- Persistent model selection and Plan/Build mode.
- Registered tool dispatch with strict argument contracts.
- Approval pauses for protected actions, followed by controlled resumption.
- Bounded error feedback and correction attempts.
- Repeated identical failed calls are guarded against.
- Durable task and execution history.

### Windows computer control

- Discover live applications and top-level windows.
- Bind authorization to the exact process and current conversation.
- Capture fresh Windows UI Automation observations.
- Read accessible text, values and supported grid information.
- Invoke controls, set values, toggle, select, expand, collapse and scroll where supported.
- Use foreground-scoped input for observed UIA targets when semantic actions are not available.
- Optional Cua Driver background snapshots and interactions when installed.
- Require separate approval for protected interactions and higher-risk escalation.
- Re-observe after actions instead of confusing input delivery with task completion.

A visible or installed application is not automatically authorized. Desktop control remains tied to a verified process and the operator's decision.

### Browser automation

The browser connector uses an isolated Chromium session through Playwright rather than reusing the operator's everyday browser profile.

Supported operations include:

- Inspecting and navigating pages.
- Locating unique controls and interacting with supported elements.
- Filling ordinary text fields and selecting dropdown values.
- Uploading approved workspace files.
- Capturing screenshots and downloading files.

Browser mutations follow the runtime's approval policy. Page content is treated as untrusted input, and password fields are not filled by the agent.

### Files, Word documents and PDFs

Chimera includes bounded workspace operations for local file work:

- List files and inspect metadata.
- Search filenames and supported text content.
- Read text files within configured size limits.
- Create new text and source files.
- Generate genuine `.docx` Word documents.
- Generate PDFs and independently reopen them to verify extracted text and page structure.
- Refuse to overwrite an existing destination through built-in create operations.

This is the foundation for workflows such as reading a source text file, turning it into a formatted document, exporting a PDF and checking the finished artifact.

### Specialist application integrations

| Integration | Current role | Requirement |
|---|---|---|
| **Blender** | Validated scene recipes, primitive-based 3D scene creation, saved BLEND projects and PNG previews. | Blender installed locally. |
| **Ghidra** | Installation discovery and bounded binary inspection, including supported strings and function analysis. | Ghidra and its required Java runtime. |
| **REA** | Built-in static artifact identification and inspection for supported binary formats. | No separate application for built-in analysis. |
| **Browser** | Isolated browser inspection and controlled web interaction. | Edge or Chrome executable and Playwright runtime. |

Connectors declare their identity, actions, argument schema, permissions, risk level, preconditions and availability. An installed application is not assumed to support every operation, and unavailable actions are reported rather than silently faked.

### Workflows, goals and reusable skills

- Multi-step workflows with dependencies and persisted run state.
- Operator-owned goals with priorities, success criteria and attached evidence.
- Evidence-aware evaluation states: pass, fail, unknown and blocked.
- Execution trace review and secret redaction.
- Reusable skill proposals with declared steps, permissions and expected outputs.
- Operator approval before a proposed skill becomes an approved reusable capability.

Chimera's learning direction is governed experience capture, not unrestricted self-modification.

---

## The workbench

The Flet desktop workbench brings the systems into one place:

| Area | What it is for |
|---|---|
| **Chat Workbench** | Persistent conversations, task requests, tool activity and Plan/Build controls. |
| **Model Bay** | Local model configuration, runtime status and provider profiles. |
| **Applications** | Installed/running application discovery and connector availability. |
| **Workflow Studio** | Structured workflow creation and execution. |
| **Capabilities** | Built-in capabilities and custom capability proposals. |
| **Goal Ledger** | Long-lived goals, success criteria and evidence. |
| **Run History** | Workflow runs, tool outcomes and execution events. |

The interface is designed around a clear distinction between planning, approved execution and verified outcomes.

---

## Approval, safety and evidence

Chimera's philosophy is not “let the model do whatever it wants.” It is **give the model useful capabilities, then make authority explicit**.

**Plan Mode** supports reasoning, preparation and permitted inspection. Protected operations remain blocked.

**Build Mode** enables governed execution. Actions that change files, application state or external resources are checked against their declared permissions and risk level. Where approval is required, Chimera pauses and shows the exact operation and arguments.

The runtime is built to:

- Validate tool arguments against registered contracts.
- Check application operations against connector manifests.
- Tie Windows interactions to a live process and fresh UI observation.
- Keep filesystem access within the selected workspace scope.
- Verify generated documents after writing.
- Distinguish success from attempted, failed, blocked or unverified work.
- Avoid silently changing model providers or escalating an action.

**The agent proposes. The operator authorizes. The runtime executes. Evidence decides.**

---

## Local-first by design

Your computer should not need to send every thought, file or task to a remote service.

- Local GGUF inference is supported through llama.cpp.
- External model APIs are optional and explicitly selected.
- Model files and inference executables remain under operator control.
- Browser automation uses an isolated profile.
- Desktop and filesystem actions run with the current Windows user's permissions.
- No hidden elevation, silent model downloads or automatic hosted fallback.

Local-first is an architectural choice, not just a badge.

---

## Install on Windows

### Requirements

- Windows 10 or Windows 11, 64-bit.
- Python 3.11 or newer for a source checkout.
- Internet access during initial dependency installation.
- WinGet for optional companion-app setup.
- A local GGUF model and matching llama.cpp executable for local inference.

### 1. Clone the repository

```powershell
git clone https://github.com/Parth-Surve2007/Chimera.git
cd Chimera
```

### 2. Run the setup helper

```bat
scripts\setup_windows.bat
```

The helper installs Chimera and its test/packaging dependencies into the active global Python user installation. It does not create a virtual environment.

When WinGet is available, the helper offers optional setup for Blender, Eclipse Temurin JDK 25 and Ghidra. Review installation prompts and Windows permission dialogs. The setup process does not download a GGUF model.

### 3. Launch Chimera

```powershell
python -m chimera
```

The first launch creates the local Chimera data directory. Source runs use `.chimera\` in the repository unless `CHIMERA_DATA_DIR` is set. Packaged runs use the current user's LocalAppData directory.

### 4. Configure a local model

1. Open **Model Bay**.
2. Select your GGUF model.
3. Select the matching `llama-server.exe` for persistent inference, or configure `llama-cli.exe` for one-shot mode.
4. Start with **APPLY LOW-MEMORY PRESET**: 4K context, 384 output tokens, 4 CPU threads and adaptive GPU offload. This is the safer starting point for a 16 GB RAM system.
5. Save the configuration and load the model. The llama-server must restart to apply changed settings.

Existing saved inference budgets are preserved. Larger context and output budgets are an explicit choice, not a silent migration.

Chimera does not silently change providers, download a model or fall back to a hosted service when local inference fails. Hosted API profiles can be configured separately and selected explicitly.

### Optional local vision

Configure a compatible local multimodal model and llama.cpp server before launching Chimera:

```powershell
$env:CHIMERA_VISION_MODEL_PATH="C:\Models\vision-model.gguf"
$env:CHIMERA_VISION_SERVER="C:\llama.cpp\llama-server.exe"
$env:CHIMERA_VISION_MMPROJ_PATH="C:\Models\mmproj.gguf"
```

The model, projector and llama.cpp build must be compatible. Vision is opt-in and does not grant desktop interaction permissions.

---

## Build a portable Windows application

From the repository root, run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ".\scripts\build_windows.ps1"
```

The script runs the configured tests and compile checks, prepares the Flet desktop client, creates the frozen application and writes:

```text
dist\
├── Chimera\
│   └── Chimera.exe
└── Chimera-Windows-portable.zip
```

To skip tests only when you have already run them and intentionally want to bypass them during packaging:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ".\scripts\build_windows.ps1" -SkipTests
```

The portable application bundles its Python runtime and Flet desktop client. GGUF model files, llama.cpp executables, Blender and Ghidra remain separate local installations.

---

## Development

Install source and test dependencies:

```powershell
python -m pip install --user -e ".[test]"
```

Enable the optional Cua Driver backend:

```powershell
python -m pip install --user -e ".[computer-use]"
```

Run the CI test selection:

```powershell
python -m pytest tests/ --ignore=tests/test_ui.py -q
```

Run the full local suite:

```powershell
python -m pytest -q
```

Compile-check the application and tests:

```powershell
python -m compileall -q chimera tests
```

### Repository map

```text
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
```

### Runtime architecture

```text
                  Desktop Workbench
                         |
                         v
                    AgentRuntime
                         |
                      TaskPolicy
                         |
          +--------------+---------------+
          |              |               |
          v              v               v
      Workspace     Authorized       Specialist
        tools       Windows apps     connectors
          |              |               |
          +--------------+---------------+
                         |
                         v
                 Persisted evidence
                 SQLite · Runs · Goals
```

---

## The roadmap: a computer agent, not another chatbot

The long-term direction is to make Chimera a capable, extensible operator for the digital workspace:

- **More applications:** deeper native connectors for office, creative, engineering, development and productivity software.
- **Richer computer use:** better handling of complex interfaces, multi-window work and applications with limited accessibility support.
- **Stronger local intelligence:** efficient model routing, model-specific tool use and local multimodal perception.
- **Long-running work:** durable plans, resumable execution, dependencies and recovery from partial progress.
- **Better verification:** independent checks for documents, media, code, scenes and application state.
- **Reusable expertise:** operator-approved skills that capture successful workflows without granting themselves new permissions.
- **A larger ecosystem:** an open connector and capability SDK so developers can teach Chimera new tools without rebuilding the agent.

The goal is not to replace every application. It is to give one agent a useful, governed way to work across them.

---

## Compatibility, honestly

Chimera is an ambitious computer-work platform, not a guarantee that every third-party application can already be automated in every situation.

- Windows desktop automation depends on the controls exposed by the target application.
- A visible application is not automatically authorized.
- Some applications expose incomplete or no useful UI Automation information.
- Blender scene generation currently targets validated primitive-based recipes rather than unrestricted modelling.
- Ghidra-backed operations depend on the external installation and Java runtime.
- Browser automation requires an installed Edge or Chrome executable and a working Playwright runtime.
- Local model quality, context limits, GPU memory and inference speed depend on the selected GGUF and llama.cpp build.
- Local vision requires a compatible multimodal model and projector configuration.

When a capability is unavailable, Chimera should say so rather than pretend the task succeeded. That discipline is what makes broader computer control worth building.

---

## Contributing

Issues, focused pull requests, tests, connector improvements and documentation fixes are welcome.

A useful contribution includes:

1. A clear problem statement and expected behaviour.
2. Tests for new tool, policy or persistence behaviour.
3. Honest capability and failure reporting.
4. No hidden hosted fallback, silent downloads or unapproved protected changes.

For application integrations, prefer a manifest-backed connector with explicit permissions, argument validation, availability reporting and tests.

---

## Useful links

- [Repository](https://github.com/Parth-Surve2007/Chimera)
- [Issues](https://github.com/Parth-Surve2007/Chimera/issues)
- [Blender](https://www.blender.org/download/)
- [Ghidra releases](https://github.com/NationalSecurityAgency/ghidra/releases)
- [Ghidra installation guide](https://github.com/NationalSecurityAgency/ghidra/blob/master/GhidraDocs/GettingStarted.md)
- [Eclipse Temurin](https://adoptium.net/installation/)
- [WinGet documentation](https://learn.microsoft.com/windows/package-manager/winget/)

---

<p align="center">
  <strong>CHIMERA</strong><br>
  <em>Local intelligence. Desktop-wide ambition. Work with evidence.</em>
</p>
