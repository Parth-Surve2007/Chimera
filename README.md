# 🐉 Chimera

### Your desktop. Your tools. Your rules.

**Chimera is a local-first desktop agent for reasoning, file work, Windows application control, and governed workflows.** It brings a local model, a tool runtime, and a focused desktop workbench into one place. You choose the model. You choose which application to authorize. Protected changes wait for your approval.

Think less “chat window with a thousand promises,” more **a capable workbench with the safety catches left on**.

<p align="left">
  <img alt="Python 3.11+" src="https://img.shields.io/badge/Python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white">
  <img alt="Windows desktop" src="https://img.shields.io/badge/Desktop-Windows-0078D4?style=flat-square&logo=windows&logoColor=white">
  <img alt="Local first" src="https://img.shields.io/badge/Architecture-Local--first-BC8B3C?style=flat-square">
  <img alt="Flet" src="https://img.shields.io/badge/UI-Flet-222222?style=flat-square">
</p>

> **Project status:** active development. Chimera has a local agent runtime, desktop workbench, bounded file tools, application connectors, workflow persistence, and Windows packaging. Some integrations remain intentionally limited or require separate native applications.

---

## ✨ What Chimera does

| Area | What is available |
|---|---|
| **Local reasoning** | Run a GGUF model through a persistent llama-server or an explicitly configured one-shot llama-cli runtime. |
| **Computer control** | Discover visible Windows applications, authorize one exact running process, inspect its UI Automation tree, and use supported semantic controls. |
| **Local files** | Read, list, inspect, and search files under the default system-drive scope. Create new text and source files after explicit approval. |
| **3D workflows** | Ask the Blender connector to create a bounded primitive-based scene and render a PNG preview. |
| **Reverse engineering** | Use REA for built-in static artifact inspection, strings, file-format detection, and available analysis backends. Connect Ghidra when installed. |
| **Multi-step work** | Run sequential workflows with tool validation, approval pauses, persistent run records, and evidence-aware outcomes. |
| **Long-term context** | Keep operator-owned goals, priorities, success criteria, and completion evidence in the Goal Ledger. |
| **Capability review** | See first-party capabilities separately from custom proposals awaiting review. |

Chimera distinguishes between a **connector** and the external application it connects to. The Blender and Ghidra connectors ship with Chimera; Blender and Ghidra themselves are separate installs.

## 🧭 The workbench

The desktop UI keeps the main navigation on the left while you move between Chat, Model Bay, Applications, Workflow Studio, Capabilities, Goals, and Run History.

- **Chat** is the continuous conversation and task surface.
- **Model Bay** manages local GGUF and explicitly selected API profiles.
- **Applications** discovers open Windows apps and reports the state of specialist connectors.
- **Capabilities** lists the built-in toolkit separately from custom capability proposals.
- **Workflow Studio** and **Run History** show persisted workflow activity.
- **Goals** stores operator-owned outcomes and evidence.

The interface uses a restrained graphite, warm-gold, and ember palette. The goal is to keep useful controls visible and decorative noise on a short leash.

---

## 🧰 Built-in capabilities

Chimera seeds a first-party capability inventory on startup. These are features shipped in the repository, not fabricated entries in the custom capability registry.

| Capability | Availability | Notes |
|---|---|---|
| Local file operations | Built in | Bounded listing, metadata inspection, filename/content search, text reading, and approval-gated file creation. |
| Windows desktop control | Built in on Windows | Uses psutil and pywinauto for process identity and UI Automation. |
| REA static analysis | Built in | Does not require a separate reverse-engineering application for its built-in parsers. |
| Ghidra integration | Connector included | Detects a local Ghidra installation. Ghidra 12.2 requires a 64-bit JDK 25 or newer. |
| Blender scene generation | Connector included | Requires the Blender desktop executable to be installed locally. |
| Sequential workflows | Built in | Runs through Chimera's governed workflow engine. |
| Goals and evidence ledger | Built in | Persistent operator-owned goals and state transitions. |

### Blender

The Blender connector accepts a bounded JSON scene recipe, validates supported primitive types and values, asks for approval, and invokes Blender with a fixed script. It does **not** run model-supplied Python code.

Current scene generation covers a small set of mesh primitives, transforms, simple materials, a camera, lighting, a saved .blend project, and a PNG preview. It is not yet a general-purpose mesh modeller.

- Official download: [blender.org/download](https://www.blender.org/download/)
- Windows package: **BlenderFoundation.Blender**

### Ghidra

Chimera includes a Ghidra connector and REA backend integration. Its built-in static parsing can still inspect supported artifacts without the native Ghidra application. Deeper Ghidra-backed work needs the external installation.

**Important:** current Ghidra 12.2 releases require a 64-bit **JDK 25 or newer**. The Windows setup helper installs Temurin 25 and downloads the official Ghidra public release archive from the NSA-maintained repository. See the [official Ghidra release notes](https://github.com/NationalSecurityAgency/ghidra/blob/master/Ghidra/Configurations/Public_Release/src/global/docs/WhatsNew.md) and [Getting Started guide](https://github.com/NationalSecurityAgency/ghidra/blob/master/GhidraDocs/GettingStarted.md).

The connector's direct arbitrary script-execution action remains unavailable. Chimera does not claim that Ghidra scripting is active merely because a Ghidra folder exists.

---

## 🗂️ Filesystem scope: no project picker

On Windows, Chimera starts with the **system drive as its default local workspace**. On a typical Windows installation this is:

    C:\

There is no separate “Attach Project” step. The local model receives the active workspace root and a workspace-relative path for the Windows Desktop when that known folder is available. For example, a Desktop file may resolve to:

    Users\YourUser\Desktop\hello.txt

The filesystem tools run with the permissions of the Windows account that launched Chimera. They do not elevate privileges or bypass Windows ACLs.

### Filesystem limits

The system-drive scope is broad, so discovery is deliberately bounded:

- Directory traversal skips inaccessible folders and does not follow symbolic links or Windows junctions.
- A listing returns at most 200 files.
- Filename search examines at most 2,000 files and returns at most 200 matches.
- Text search scans at most 2,000 files and 50 MB of candidate text.
- Individual text reads and new-file contents are limited to 2 MB.
- New files must use a supported text or source-code extension.
- Existing files are never overwritten by the built-in create-file operation.

**Reads are read-only. Writes are approval-gated.** Plan Mode cannot perform protected changes. In Build Mode, Chimera shows the exact operation and arguments before execution. You approve or reject that exact action.

Chimera does not provide an unrestricted shell or automatically execute generated files. The default drive scope is not a promise that every protected Windows or application file is readable.

---

## 🖥️ Windows application control

The Applications page discovers currently visible, titled Windows application windows at runtime. It is not a hard-coded list of executables.

The control flow is deliberately staged:

1. Refresh the live application inventory.
2. Authorize one exact running process for the current conversation.
3. Capture a fresh UI Automation observation.
4. Use supported semantic actions against controls from that observation.

Supported interactions include reading text, invoking controls, setting values, toggling, selecting, expanding, and collapsing UI elements when the target application exposes the corresponding UI Automation patterns.

Chimera does **not** currently use global blind keyboard injection, blind focus changes, or remote window closing. Some applications expose very little UI Automation information, so discoverability does not guarantee that every control in that application can be automated.

### Dependencies

The Windows dependency set includes:

- **psutil** for process and window identity information.
- **pywinauto** for Windows UI Automation.
- **Flet's desktop runtime** for the native workbench.

These are installed by the setup helper. No separate computer-control SDK needs to be downloaded manually.

---

## ⚙️ Install on Windows

### Requirements

- Windows 10 or Windows 11, 64-bit.
- Python 3.11 or newer for a source checkout.
- Internet access during initial dependency and optional companion-app installation.
- [WinGet](https://learn.microsoft.com/windows/package-manager/winget/) for the optional automated companion-app setup.
- A local GGUF model and matching llama-server or llama-cli executable for local inference.

### 1. Clone the repository

Open Command Prompt:

    git clone https://github.com/Parth-Surve2007/Chimera.git
    cd Chimera

### 2. Run the Windows setup helper

    scripts\setup_windows.bat

The helper installs Chimera and its test/packaging dependencies into the active global Python user installation. **It does not create a virtual environment.**

When WinGet is available, the helper offers to install:

- Blender through **BlenderFoundation.Blender**.
- Eclipse Temurin JDK 25 through **EclipseAdoptium.Temurin.25.JDK**.
- The latest official Ghidra public ZIP release through **scripts\install_ghidra.ps1**.

The setup process is interactive. Review the installation prompts and Windows permission dialogs. It does not download a GGUF model. If WinGet is unavailable, install [Blender](https://www.blender.org/download/), [Temurin](https://adoptium.net/installation/), and [Ghidra](https://github.com/NationalSecurityAgency/ghidra/releases) manually.

### 3. Launch Chimera

    python -m chimera

The first launch creates the local Chimera data directory. Source runs use .chimera\ in the repository unless CHIMERA_DATA_DIR is set. Packaged runs use the current user's LocalAppData directory.

### 4. Configure a local model

1. Open **Model Bay**.
2. Select your .gguf model.
3. Select the matching llama-server.exe for persistent inference, or explicitly configure llama-cli.exe for one-shot mode.
4. Set the context and output budget to match your model and available memory.
5. Save the configuration and load the model.

Chimera does not silently change providers, download a model, or fall back to a hosted service when a local runtime fails. Hosted API profiles can be added separately, but they are used only when explicitly selected for the conversation.

---

## 🏗️ Build a portable Windows app

The repository includes a PyInstaller build script. From the repository root, run this in Command Prompt:

    powershell -NoProfile -ExecutionPolicy Bypass -File ".\scripts\build_windows.ps1"

The script runs the tests and compile checks, prepares the Flet desktop client, creates the frozen app, and writes:

    dist\
    ├── Chimera\
    │   └── Chimera.exe
    └── Chimera-Windows-portable.zip

Use -SkipTests only when you have already run the test suite and intentionally want to skip it during packaging:

    powershell -NoProfile -ExecutionPolicy Bypass -File ".\scripts\build_windows.ps1" -SkipTests

The portable application bundles its Python runtime and Flet desktop client. The optional Blender and Ghidra companion applications remain separate installs.

---

## 🧪 Development

Install the source and test dependencies with:

    python -m pip install --user -e ".[test]"

Run the CI test selection:

    python -m pytest tests/ --ignore=tests/test_ui.py -q

Run the full local test suite when working on the UI:

    python -m pytest -q

Compile-check the application and tests:

    python -m compileall -q chimera tests

### Project map

    chimera/
    ├── agent_runtime.py        # Sessions, tool policy, approvals and execution loop
    ├── local_model.py          # Local llama.cpp reasoning and prompt construction
    ├── tools.py                # Bounded workspace tools
    ├── builtin_capabilities.py # First-party capability inventory
    ├── computer_use.py         # Windows desktop discovery and interaction
    ├── computer_runtime.py     # Process authorization and UIA evidence
    ├── applications/           # Connector SDK, registry and app integrations
    ├── reverse_engineering/    # REA static analysis engine
    ├── goals.py                # Operator-owned goals and evidence
    ├── workflow.py             # Governed workflow execution
    └── ui.py                   # Flet desktop workbench

    scripts/
    ├── setup_windows.bat       # Global Python dependencies and optional companions
    ├── install_ghidra.ps1      # Official Ghidra release installer
    └── build_windows.ps1       # Tests, Flet client prep and portable package

### Runtime flow

    Desktop workbench
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

---

## 🔒 Design principles

| Principle | How Chimera applies it |
|---|---|
| **Local first** | Local GGUF inference is the default path. Hosted providers are explicit selections, not failover targets. |
| **No silent execution** | Protected operations pause for Build Mode and exact operator approval. |
| **Least surprise** | The agent uses registered tools and actual results; it must not invent successful outcomes. |
| **Bounded operations** | File reads, searches, process identity, and generated artifacts have explicit limits. |
| **Evidence before action** | Desktop interactions require a fresh observation and a process authorized for the current conversation. |
| **Honest capability status** | A connector can be present while its external companion application is missing or a specific action remains unavailable. |

---

## 🧭 Current limitations

Chimera is a growing engineering project, not a claim that every desktop application is fully automatable.

- Windows desktop automation is platform-specific and depends on the target app exposing useful UI Automation controls.
- A visible application is not automatically authorized. Authorization is per conversation and exact process identity.
- Some applications expose no semantic controls or expose only a partial UIA tree.
- Blender generation currently targets simple, validated primitive-based scenes.
- The Ghidra connector's direct script-execution action remains unavailable. Ghidra-backed REA operations depend on the external installation and the correct JDK.
- Browser automation and universal control of arbitrary third-party applications are not advertised as ready capabilities.
- Local model quality, context limits, GPU memory, and inference speed depend on the selected GGUF and llama.cpp build.

---

## 🧩 Contributing

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
