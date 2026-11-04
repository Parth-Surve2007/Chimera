# Hermes Agent research notes for Chimera

**Research date:** 4 November 2026  
**Reference implementation:** [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent)  
**Official documentation:** https://hermes-agent.nousresearch.com/docs

## Recommendation

Do not vendor Hermes or replace AgentRuntime with it. Hermes is a mature general agent platform with substantially broader surface area than Chimera, but it is not a drop-in engine for Chimera's governed Windows application control. Preserve Chimera's local-first provider router, AgentRuntime execution authority, application authorization, workspace boundaries, and evidence-based task completion.

Adopt selected architecture patterns behind Chimera-native interfaces.

## What is worth adopting

### 1. Small stable tool catalogue, progressive disclosure for procedural knowledge

Hermes groups registered functions into toolsets and can defer non-core tool schemas behind tool-search/describe/call bridge functions. Its skills system uses progressive disclosure: list compact names and descriptions first, then load one skill's full content or a specific reference only when it is relevant.

Chimera's built-in tool surface is smaller and should stay directly schema-constrained for now. The first Hermes-inspired slice exposes:
- `agent.skill_list`: compact summaries of operator-approved learned skills.
- `agent.skill_read`: load one skill by exact ID, only when approved.

Skill contents are guidance, not executable code, not authorization, and not proof that a previous workflow will work on the current application version. Current application state must be rediscovered, tool schemas validated, approvals obtained, and postconditions verified.

### 2. Persistent procedural memory, not just conversation transcripts

Hermes distinguishes long-lived curated memory from searchable session history and procedural skills. Chimera already has `ExperienceLearningEngine`, `SkillStore`, and workflow-learning foundations. The missing connection was an on-demand runtime path from approved skills into the model context. The new skill tools bridge that gap without inserting every skill body into every prompt.

Next steps should improve the quality of learned skills: require fully verified task traces, retain app/version/preconditions and known failure modes, parameterize task-specific arguments, generate replay tests, and invalidate stale skills when their app version or observation schema changes.

### 3. A robust model/tool loop with explicit repair and bounded retries

Hermes' agent loop separates model transport, response normalization, tool dispatch, retries, context compression, persistence, and observability. Chimera should preserve its own deterministic policy boundary but borrow the principle that malformed model output is a recoverable protocol error, not an excuse to pretend a tool ran.

The new decision repair path retries one malformed decision envelope with a strict schema reminder. A result-shaped object containing `tool`, `arguments`, `result`, or `status` is never treated as a completed action. Only a valid `message` or `tool_call` decision can proceed to registered tool validation.

### 4. Durable session history and context management

Hermes uses SQLite-backed session state and FTS5 full-text search, plus context compression and session lineage. Chimera already persists sessions, events, tasks, checklists, approvals, and workflow evidence in SQLite. The near-term priority is to preserve task identity and verified state across turns/restarts; searchable historical recall and evidence-aware context compaction can follow without changing the provider router.

## What not to copy

- Hermes' quickstart currently recommends at least 64K context for its full multi-step workflow. Chimera's local Llama 3.2 3B configuration is currently tuned around an 8K context budget on an RTX 3050 Laptop GPU with 4 GB VRAM. Do not import Hermes' context assumptions or claim equivalent model capability.
- Do not transplant Hermes' 70+ tool registry, 28 toolsets, messaging gateway, terminal backends, or plugin ecosystem wholesale. Add only capabilities that Chimera can govern and certify.
- Do not use generic terminal execution as a substitute for application-specific Windows authorization, UI Automation, scoped pointer/keyboard input, and independent verification.
- Do not let learned skills store or replay stale screen coordinates, process IDs, window handles, or approval tokens.

## Research links

- Architecture overview: https://github.com/NousResearch/hermes-agent/blob/main/website/docs/developer-guide/architecture.md
- Tools and toolsets: https://hermes-agent.nousresearch.com/docs/user-guide/features/tools/
- Skills system: https://hermes-agent.nousresearch.com/docs/user-guide/features/skills
- Persistent memory: https://hermes-agent.nousresearch.com/docs/user-guide/features/memory
- Tool search: https://hermes-agent.nousresearch.com/docs/user-guide/features/tool-search
- Quickstart and local model context guidance: https://hermes-agent.nousresearch.com/docs/getting-started/quickstart
