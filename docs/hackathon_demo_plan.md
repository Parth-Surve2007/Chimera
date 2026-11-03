# Meta Developer Global Hackathon Demo Plan

## Product thesis

Chimera is a local-first computer intelligence layer, not another chat UI. It connects a selected model to registered application capabilities through one persistent, auditable execution authority.

**Models propose. AgentRuntime validates and authorizes. Connectors act. Chimera verifies the artifacts.**

The long-term ambition is broad computer intelligence across creative, engineering and productivity applications. The hackathon submission should prove one narrow workflow deeply rather than claim that every desktop application is already automated.

## Demonstration objective

Ask Chimera:

> Create a small, stylized 3D product concept in Blender and give me both the editable project and a rendered preview.

### Demonstration sequence

1. **Choose the inference mode.** Start with a configured local GGUF profile and run the real local AgentRuntime protocol verification. No hosted fallback or model download is permitted.
2. **Choose a workspace and goal.** State the output location explicitly. Use Build Mode for the protected file-creation action.
3. **Inspect available capabilities.** Chimera discovers the local Blender executable and exposes only actions present in the connector manifest.
4. **Plan the scene.** The model proposes a bounded JSON recipe using supported primitives, transforms and RGB materials. It does not author or execute arbitrary Python.
5. **Authorize the exact action.** AgentRuntime validates the connector, argument schema, task mode and permission. The operator approves the exact output paths and recipe before Blender writes files.
6. **Execute locally.** The Blender connector invokes the local Blender executable in background mode with a fixed script and a JSON configuration.
7. **Verify the deliverables.** Chimera checks that both the .blend project and PNG render exist and are non-empty. The result and permission decision are recorded in the task timeline.
8. **Show model portability.** When credentials and network access are available, explicitly select Meta Muse Spark and repeat the same connector path. The model provider changes; the execution authority and connector do not.

## What this proves

- A local model can participate in a structured agent decision protocol.
- Model providers are replaceable without moving application execution into the provider.
- A model can select a registered application action through a declared tool schema.
- Protected file creation requires explicit operator approval.
- The application connector uses a constrained recipe, not arbitrary model-generated code.
- The output is an actual application project plus a render, not just a text answer.
- Execution status and evidence are persisted by the runtime.

## Meta integration

The Meta Model API provider targets Muse Spark and is opt-in. The intended live demonstration uses a valid Meta API credential and network access. Meta is an external reasoning provider, not part of the offline execution claim.

The provider's tool catalog includes eligible workspace, computer and discovered application actions. Meta proposes a call; AgentRuntime remains responsible for schema validation, action registration, mode/risk checks and approvals. CI uses a local mock HTTP server to verify the protocol and end-to-end dispatch; that test is not a live Meta API acceptance test.

Optional Meta perception/generation APIs should be added only after their live request, response handling, provenance, privacy mode and failure behavior are verified. A parser or mocked API response alone is not evidence of a live integration.

## Current scope and honest limitations

- **Blender:** the initial connector discovers Blender and creates primitive-based scenes with a PNG preview. It is not yet a general 3D modeller and does not provide arbitrary mesh editing, node-graph authoring, animation or unrestricted Blender UI automation.
- **Other applications:** Ghidra and REA connectors exist. Word, Excel, PowerPoint, Paint, PCB/CAD and broad browser/desktop automation are future connectors, not current completed capabilities.
- **Computer use:** Windows UI Automation is partial and process-authorized. Global keyboard/mouse injection is intentionally disabled. Linux/macOS computer control is not implemented.
- **Learning:** Chimera captures traces, redacts secrets and can propose reusable skills/workflows for operator review. Automatic skill replay, versioned promotion, regression testing and model self-training are not current capabilities.
- **Local models:** GGUF inference is supported through llama.cpp. The model file must be supplied/configured by the operator; Chimera does not silently download models or fall back to hosted inference.
- **Meta:** CI protocol coverage is not proof of a live API request. Verify the live credential path on the target machine before describing Meta as live-tested.

## Judge-facing narrative

**Problem:** models, applications and user context are fragmented. An answer from an AI system is not the same as completing a task in the user's software.

**Approach:** Chimera separates intelligence from execution. Providers are replaceable; application tools are typed capabilities; one runtime enforces permissions, approvals and evidence.

**Differentiator:** local-first operation, model choice, application execution, verified artifacts and governed learning are designed as one system rather than a collection of unrelated demos.

**Evidence:** demonstrate the local protocol check, the registered Blender tool, the approval event, the generated .blend and PNG, and the persisted task result. Only show a live Meta run when the actual API credential and request have been tested.

## Pre-submission acceptance checklist

- [ ] Run the latest Windows and Ubuntu CI jobs and capture their exact commit SHA and result.
- [ ] On the target Windows machine, verify the configured local GGUF through the real inference path.
- [ ] Confirm the selected local model is the active conversation model and no hosted profile is selected.
- [ ] Run Blender discovery against the installed local executable.
- [ ] Complete one live scene-creation task and open the generated .blend file in Blender.
- [ ] Open the PNG preview and confirm it matches the project.
- [ ] Verify Plan Mode blocks scene creation and Build Mode requires explicit approval.
- [ ] Verify an existing output file is not overwritten.
- [ ] Test Meta Muse Spark with a real credential, inspect provider usage/provenance, and confirm the tool call still passes through AgentRuntime.
- [ ] Record a short screen capture showing the prompt, approval, execution, verified files and final response.
- [ ] Ensure the submission describes unimplemented apps and learning behavior as roadmap, not completed features.
