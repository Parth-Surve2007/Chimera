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

## Permanent safety invariants

- AgentRuntime is the only execution authority.
- Plan Mode is read-only for protected actions; Build Mode still requires existing permission and approval checks.
- No access outside the active workspace scope.
- No unregistered shell/Python/code execution.
- No blind global keyboard/mouse injection or foreground stealing.
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

### Current generic Windows interaction slice
- Execute actions through a validated, fresh UIA element reference and exact authorized process identity.
- Keep read-only Text, RangeValue and Grid observations separate from state-changing actions.
- Support ValuePattern, RangeValuePattern, InvokePattern, TogglePattern, SelectionItemPattern, ExpandCollapsePattern, ScrollPattern and ScrollItemPattern where the live control exposes them.
- Require approval for every UIA state change; do not substitute blind global keyboard/mouse injection or a Win32 mutation fallback.
- Verify readable/range state after actions and require a fresh observation after operations such as InvokePattern where the control does not expose a deterministic postcondition.

### Next: Windows capability ecosystem
- Add native Office automation and real spreadsheet/presentation operations.
- Add browser control through a controlled protocol.
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
