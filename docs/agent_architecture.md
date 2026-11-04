# Chimera Agent Architecture

**Updated:** 4 November 2026

## Product definition

Chimera is a local-first computer-work agent. A user gives it an outcome; Chimera discovers applicable capabilities, builds a bounded plan, performs authorized actions, observes their actual results, verifies the requested outcome, and reports what succeeded or what remains blocked.

It is not defined by a chat interface, one file writer, or a list of hard-coded applications. Broad Windows application coverage is a product ambition that must be earned through tested capability interfaces.

## Execution contract

```text
User outcome -> understand intent -> discover capabilities -> bounded plan
-> validate schema, mode, scope, permissions and approval
-> execute one registered action -> capture result/evidence -> verify postcondition
-> update task progress -> continue, recover safely, or explain blocker
```

AgentRuntime is the execution authority. The model proposes semantic decisions; it never directly executes code or grants itself permission. Model-generated text is not evidence that an action happened.

## Lessons from open-source agent systems

### OpenCode
- Persistent task checklists through dedicated read/write tools, separate from side-effecting tools.
- Separate Plan and Build responsibilities and permission profiles.
- A repeated model/action loop that continues from actual tool results.
- Context compaction to preserve active work without retaining every old token.

References: https://github.com/anomalyco/opencode and https://opencode.ai/docs/

### SWE-agent
- Purpose-built file viewing and editing tools.
- Bounded line windows, targeted search and concise results.
- Interface design that reduces the cognitive burden on the model.

Reference: https://github.com/SWE-agent/SWE-agent

### OpenHands and Agents SDK
- Separate reasoning loops from tool implementations.
- Event-driven state, observable tool results, guardrails and traceability.
- Explicit and scoped delegation.

References: https://github.com/All-Hands-AI/OpenHands and https://github.com/openai/openai-agents-python

### Application coverage ladder (vNext)

Chimera must not treat "computer use" as one mouse-and-keyboard backend. The same user outcome can require very different control surfaces. The runtime should choose from this ordered ladder using observed app identity and available evidence:

1. **Native application adapter**: documented APIs, COM/WinRT, Office object models, application SDKs, or an app's own file/database interface. Prefer this for structured edits and repeatability.
2. **Registered application connector**: a versioned manifest with discoverable actions, argument schemas, permission declarations, preconditions, execution context, and independent postconditions.
3. **Windows UI Automation**: inspect accessible controls and use supported patterns such as Text, Value, Invoke, Toggle, Selection, RangeValue, Grid, and Scroll. Never infer support from an application name alone.
4. **Browser automation**: use the isolated Playwright browser profile and semantic DOM/accessibility locators before pixel interaction.
5. **Scoped visual interaction**: use only a fresh element reference, a rectangle contained by the exact authorized HWND, exact foreground PID/HWND checks before every input packet, and explicit approval. This is for custom surfaces that expose no useful semantic controls, not the default interaction strategy.
6. **Validated file-format operations**: edit DOCX/PDF/CSV/JSON and other supported artifacts through format-aware libraries when that is the actual task, then reopen and verify the output.
7. **Operator handoff**: when none of the above is grounded or safe, explain the missing capability and preserve resumable task state.

An application adapter is accepted only when it has: a stable app identity/version match; an explicit capability manifest; strict schemas; a least-privilege permission set; deterministic preflight; cancellation/timeout behavior; bounded retries; audit events; and an independent postcondition test. Connector discovery is not permission, a successful API return is not always task completion, and a generic UIA tree is not proof that every app workflow is covered.

This mirrors the useful UFO² design lesson: separate the desktop orchestrator from application-specific execution, and combine Windows UI Automation with native APIs rather than betting on vision-only control. The Windows accessibility guidance also recommends inspecting the actual UIA representation, because each app's exposed controls and patterns determine what an automation client can do. Hermes contributes a useful broad toolset and progressive skill loading pattern, but Chimera keeps all execution inside AgentRuntime's permission and evidence boundary.

### Optional local visual perception

A user may configure a separate multimodal GGUF without changing Chimera's text model:

- `CHIMERA_VISION_MODEL_PATH`: absolute path to a locally installed vision-capable GGUF.
- `CHIMERA_VISION_SERVER`: absolute path to the matching local `llama-server` executable. If omitted, Chimera may use the already configured local llama-server executable path.
- `CHIMERA_VISION_MMPROJ_PATH`: optional absolute path to a separate multimodal projector GGUF when the selected model requires one.
- Optional tuning: `CHIMERA_VISION_CONTEXT`, `CHIMERA_VISION_THREADS`, `CHIMERA_VISION_GPU_LAYERS`, and `CHIMERA_VISION_TIMEOUT`.

No model is downloaded. The vision server starts only when an approved visual inspection is requested. Images are resized and encoded as bounded JPEG data URLs and sent only to the local llama-server. Remote image URLs, hosted providers, and silent fallback are rejected. Missing or incompatible multimodal assets produce a truthful unavailable/failed result.

The local observer can describe a screenshot, locate candidate controls, and compare visible state with an expected result. Its output is still model-generated observation, not a proof of side effects. Chimera must combine it with fresh UIA/process/window evidence, actual file state, and deterministic application/file-format checks before reporting completion. Screenshots are privacy-sensitive: `computer.inspect_visual_target` requires exact operator approval and refuses to capture a background or foreign window.

### Advisory desktop application profiles

The generic desktop agent includes conservative descriptive profiles for common Windows applications (Paint, Notepad, File Explorer, Word, Excel, PowerPoint, VS Code, GIMP, Blender, Edge and Chrome). Profiles identify likely control roles and preferred semantic operations, such as a Paint canvas, an Excel grid or a Word document surface. They are matched against the authorized process executable and observed window titles, then attached to the current observation.

Profiles are hints only: they grant no permissions, contain no reusable coordinates, and cannot authorize or replay actions. Current process identity, window ownership, UIA references, supported patterns, operator approval and post-action evidence always take precedence. Profiles help the model ask for the right observation; they do not claim that a workflow is certified for that application version.

Operator-owned profiles are loaded from `application_profiles.json` in Chimera's data directory. Set `CHIMERA_APP_PROFILES_PATH` to use an explicit file; otherwise Chimera uses `CHIMERA_DATA_DIR`, `%LOCALAPPDATA%\\Chimera` on Windows, or `.chimera` in the working directory. The JSON file is validated and replaced atomically when written by the profile store.

A minimal profile looks like this:

```json
[
  {
    "profile_id": "studio.custom_editor",
    "version": 1,
    "display_name": "Studio Editor",
    "match": {
      "executable": ["studio.exe"],
      "title_contains": ["Studio Editor"]
    },
    "target_hints": [
      "Find the main document surface separately from side panels."
    ],
    "preferred_operations": ["inspect_elements", "read_text"],
    "limitations": ["Custom extensions may render controls outside UI Automation."]
  }
]
```

Only descriptive match rules, target hints, preferred-operation names and limitations are accepted. Unknown fields are discarded. Profiles are capped at 100 entries and bounded per field. Duplicate IDs and attempts to shadow built-in profile IDs are ignored or rejected. A malformed file disables custom profiles for that launch while preserving built-ins; it never changes execution permissions.

### Multi-window UIA observation and context packing

- A computer session may have several top-level windows. The agent first obtains the authorized window inventory and selects one exact HWND by title/purpose before inspecting its control tree.
- UIA traversal is bounded by depth (0-8) and element count (1-500). Each reference is bound to the authorized process, selected HWND, observation generation, and a short expiry.
- An empty UIA tree or a tree without usable runtime IDs is not called a successful semantic inspection. Chimera falls back to read-only Win32 control metadata and marks the limitation.
- Local-model context packing ranks visible, enabled canvas/document/custom surfaces and preserves their exact fresh reference IDs, bounds, supported patterns, and available actions. A toolbar label or stale reference is not substituted for the actual target.
- Model context is lossy by design, so all consequential actions still revalidate the original runtime reference. The model's summary never becomes authority.

### Scoped custom-canvas drawing

- computer.visual_action can use stroke for one connected path or multi_stroke for 1-16 distinct pen-up paths in one approval, with at most 256 normalized points total.
- A target must be a fresh, visible, enabled UIA element with a usable rectangle fully contained in the authorized application window.
- The broker verifies the foreground window and PID before each pointer event, releases held buttons on abort, and returns before/after pixel hashes and changed-pixel fraction.
- Pixel change proves that the target region changed, not that the requested semantic drawing is correct. Chimera must not claim "house drawn" from accepted input or pixel-diff evidence alone; semantic verification needs a visual-capable model or app-specific inspection.
- The approval replay binds a launched packaged app to the exact HWNDs returned by the launch observation. WindowsApps execution aliases may resolve to a different final image path; any alias exception requires the known WindowsApps path shape, a matching app/window identity, and the exact live HWND/PID. Shared host processes are restricted to those launch-observed windows.

## Designing for a 3B local model

A compact model should not solve the whole job in one completion. The runtime must break work into bounded decisions and externalize state.

1. Schema-first decisions: strict message/tool-call envelopes and argument validation.
2. Relevant tools only: expose a small, task-relevant catalogue instead of every connector.
3. One consequential action at a time: execute, return the real result, then reason again.
4. Externalized progress: keep task checklist, steps, approvals, results and evidence in SQLite.
5. Deterministic bookkeeping: runtime owns task IDs, sequencing, state transitions, retry limits and cancellation.
6. Grounded recovery: classify failure and only retry when safe and useful.
7. Evidence-based completion: verify files, hashes, process windows or application-specific postconditions.
8. Bounded context: retain objective, active checklist, relevant schemas and recent evidence.
9. No hidden model fallback: provider/runtime changes require explicit operator choice.
10. Repetition defenses: detect both user-prompt echoes and duplicate prior assistant answers, retry once with focused recovery instructions, and return an honest evidence-based fallback if the model repeats itself again.
11. Tool-loop enforcement: do not execute an identical tool call again after it has already failed in the same user turn; allow one alternative-capability recovery before stopping repeated failures.

Persistent llama-server inference sends role-separated messages through `/v1/chat/completions`, allowing llama.cpp to apply the GGUF's native chat template. Agent decisions use schema-constrained JSON on that endpoint. The one-shot CLI path remains explicit and uses its own constrained prompt mode.

The Model Bay high-compute preset starts from 8K context, 768 output tokens, 8 CPU threads and adaptive GPU layer placement (`-ngl auto`). This is a starting configuration, not a guarantee that every model/build fits; llama.cpp's memory fit and the operator's available VRAM determine practical offload.

A checklist is progress metadata, never permission to execute and never independent proof of success. The 3B model can still misplan or select the wrong tool; the harness improves structure and recoverability, not the model's intrinsic reasoning capacity.

## Windows application interaction strategy

No universal interface reliably controls every Windows application. Select the strongest available surface for the requested action:

1. Native application APIs, SDKs, COM automation or manifest-backed connectors.
2. Accessibility and UI Automation using fresh observations and exact process authorization.
3. Controlled browser automation for supported browser workflows.
4. Filesystem and validated document-format libraries where appropriate.
5. Operator-assisted interaction when a safe supported surface does not exist.

Do not silently fall back to a weaker interaction method after a stronger method fails. Surface the blocker and a safe alternative.

Windows UI Automation varies by application. Win32, WPF and WinForms often expose semantic controls; custom-rendered, elevated, remote, game, graphics, CAD and some Electron applications may expose incomplete control trees. Broad coverage requires live discovery, connector manifests, action-specific schemas, process identity, evidence, and a reproducible app/version test matrix.

Packaged Windows apps are not always represented by a discoverable executable path. The Windows app inventory reads Start-menu entries and their registered Application User Model IDs (AUMIDs); launch uses the validated `shell:AppsFolder\\<AUMID>` activation route after the normal application-launch approval. This improves discovery for apps such as Paint, Photos and Calculator, but it does not provide semantic control of every custom-rendered canvas or prove that every Start-menu item is automatable.

## Controlled browser automation

- The browser connector launches a dedicated visible Edge/Chrome process with an isolated profile. It does not attach to or reuse the operator's everyday browser profile.
- Playwright Sync API objects are owned by one connector worker thread. Calls from agent/UI threads are serialized onto that worker, with the calling conversation and workspace context explicitly rebound.
- Page inspection returns bounded text and interactive-element metadata tagged as untrusted website content. It is observation, not policy.
- Navigation and state-changing page operations require approval. Supported actions include unique-control click, editable non-password text fill, allowlisted key presses, exact native HTML select selection, workspace-confined file upload/download, and PNG screenshot capture.
- Uploads require one exact HTML file input and an existing regular file inside the selected workspace. Downloads and screenshots are written only inside that workspace.
- Browser screenshots verify the PNG signature and saved bytes against the browser-returned payload. Download/upload results report the actual file metadata.
- Browser support depends on a locally installed Edge/Chrome executable and Playwright runtime; the connector does not download browser engines. Custom JavaScript comboboxes may need explicit click/keyboard actions rather than the native-select tool.

## Graceful native-window close

- The computer.close_window action requires Build Mode and exact operator approval.
- At approval replay, AgentRuntime checks that the computer session belongs to the current conversation. ComputerSessionManager requires a fresh observation and confirms the HWND still belongs to the exact authorized PID.
- The Windows backend sends WM_CLOSE to that HWND, then checks whether the window disappears.
- It never calls TerminateProcess, taskkill, or a forced process-kill API. If the application keeps the window open (for example, an unsaved-work prompt), the result is uncertain with a manual handoff requirement, not a false success.

## Verified document output

- `workspace.create_pdf` creates a genuine PDF with an embedded ReportLab font where available and refuses overwrites.
- PDF creation requires Build Mode and the exact pending approval.
- The document writer reopens the artifact with pypdf, checks the PDF signature, page count, and normalized extracted-text equality.
- AgentRuntime independently reopens the approved output and verifies its bytes, SHA-256, page count, and extracted text before treating the tool result as completed.
- When the system drive is the workspace, `Desktop/...` resolves through the user's configured Windows Desktop known folder (including redirected Desktop locations); all resolved paths remain inside the workspace root.
- This is a file-format creation capability. It does not claim that the source Notepad application was read; the Notepad-to-PDF benchmark still requires a live, authorized UIA read of the actual editor content before PDF creation.

## Permanent safety invariants

- AgentRuntime is the only execution authority.
- Plan Mode is read-only for protected actions; Build Mode still requires existing permission and approval checks.
- No access outside the active workspace scope.
- No unregistered shell/Python/code execution.
- No unscoped keyboard/mouse injection, background-window input, clipboard injection, or global hooks.
- A scoped Windows input broker may use short SendInput packets only after exact operator approval, against a fresh UIA element rectangle fully contained by the authorized HWND, while that exact HWND and PID remain foreground before every packet.
- Graceful window closing is a distinct approved operation: send WM_CLOSE to one exact, freshly observed HWND belonging to the conversation-authorized process; verify disappearance and never force-terminate the process.
- Browser pages are untrusted data. Browser mutations require their own approval and are limited to the isolated profile and selected workspace.
- Scoped input supports bounded click/double-click/stroke/text/key/scroll operations. It aborts on focus/identity changes, releases held buttons, captures before/after hashes, and reports uncertainty when pixel changes cannot be verified.
- No silent local-to-hosted fallback or automatic model download.
- Preserve audit events for calls, approvals, outputs and verification.

## Roadmap

### Current slice
- SQLite-backed todo checklist with stable IDs and statuses.
- Model-facing todo read/write tools, distinct from side-effecting capabilities.
- Workbench checklist rendering from persisted state.
- Safe DOCX cleanup that cannot delete a path when exclusive creation loses a race.

### Next: long-horizon execution
- Keep task identity across user turns and application restarts.
- Add plan revisions, dependencies and preconditions.
- Detect repeated action loops and bounded repair/replanning.
- Resume from approval checkpoints and interrupted jobs.
- Define postcondition/evidence contracts for each action family.
- Add relevant capability routing and context compaction.

### Scoped visual interaction broker (current implementation)
- Model-facing `computer.visual_action` accepts bounded, normalized coordinates relative to a fresh UIA element reference.
- The reference is revalidated against the live process identity, exact HWND, observation generation and expiry.
- The UIA element rectangle must be fully contained in the authorized top-level window rectangle.
- Build Mode and the exact pending approval are required for all visual input.
- The input broker activates only the approved target and verifies the exact foreground HWND/PID before every input packet. Focus changes abort the remaining sequence.
- Supported bounded operations: click, double-click, draw stroke, Unicode text entry to editable UIA controls, named key chords and directional scroll.
- Before/after pixel hashes and changed-pixel estimates are returned. Accepted input is not equated with a verified application outcome; the agent must inspect fresh state and verify the requested postcondition.

### Current generic Windows interaction slice
- Execute actions through a validated, fresh UIA element reference and exact authorized process identity.
- Keep read-only Text, RangeValue and Grid observations separate from state-changing actions.
- Support ValuePattern, RangeValuePattern, InvokePattern, TogglePattern, SelectionItemPattern, ExpandCollapsePattern, ScrollPattern and ScrollItemPattern where the live control exposes them.
- Require approval for every UIA state change. Use the scoped input broker only for observed controls/custom canvases that lack a suitable semantic pattern; do not use unscoped input or a Win32 background-mutation fallback.
- Verify readable/range state after actions and require a fresh observation after operations such as InvokePattern where the control does not expose a deterministic postcondition.

### Next: Windows capability ecosystem
- Add native Office automation and real spreadsheet/presentation operations.
- Add connectors for VS Code, file managers, media, CAD/EDA and more.
- Add capability discovery, installation, tests and user-managed permissions.

### Acceptance metrics
- Structured-decision validity and correct tool-selection rates.
- Tool success and safe-abstention rates.
- Verified end-to-end completion rate and recovery success.
- Duplicate side-effect rate and approval-bypass count (must remain zero).
- Context tokens and inference latency per completed task.
- Named application/version/action coverage backed by repeatable acceptance tests.

A connector stub or mocked unit test is not evidence of end-to-end application support.
