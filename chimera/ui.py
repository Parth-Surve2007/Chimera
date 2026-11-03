"""Monochrome Flet command console for Chimera."""
import sys, asyncio, json, os
from pathlib import Path
from .review import ReviewService
from .workflow import CapabilityWorkflow
from .behavior import VirtualBoxBackend
from .langflow_studio import LangflowService, LangflowStatus
from .server_provider import load_policy, save_policy
from .model_hub import ModelProfileStore, CredentialStore, OpenAICompatibleAdapter, ModelProfile
from .audit import AuditStore
from .local_model import config_from_env, check_status, save_persisted_config, LocalModelProvider, load_persisted_config, config_data_dir, config_source
from .agent_loop import execute_objective
from .tools import CAPABILITY
from .agent_runtime import AgentRuntime, AgentSessionStore
from .goals import GoalStore
from .workflow_integration import AgenticWorkflowOrchestrator
try: import flet as ft
except ImportError: ft = None

BG, SURFACE, ELEVATED, BORDER = "#0A0D0B", "#111713", "#1A211C", "#2B342E"
TEXT, SECONDARY, MUTED, DISABLED, FOCUS = "#F1F2EC", "#B6B7AB", "#788175", "#465047", "#C49A6C"
ACCENT = FOCUS

STATE_COLORS = {"pass": "#8AAE9C", "fail": "#D9897A", "unknown": "#C7A66A", "blocked": "#B77D9A"}

def _upper(value, default="UNKNOWN"):
    """Format optional persisted/status values without allowing the UI to crash."""
    return str(value or default).upper()

def validate_workspace_path(selected):
    if not isinstance(selected, (str, Path)) or not str(selected).strip():
        raise ValueError("workspace path cannot be empty")
    path = Path(str(selected)).expanduser().resolve()
    if not path.exists():
        raise ValueError("workspace directory does not exist")
    if not path.is_dir():
        raise ValueError("workspace path must be a directory")
    if not os.access(path, os.R_OK):
        raise ValueError("workspace directory is not readable")
    return path

def persisted_evaluation(candidate, audit):
    """Read-only projection of persisted workflow evidence; never evaluates or recomputes it."""
    run_id = getattr(candidate, "workflow_run_id", None)
    if not run_id:
        return None, []
    try:
        events = audit.workflow_events_for(run_id)
    except Exception as exc:
        return {"status": "unavailable", "reason": str(exc)}, []
    report = None
    for stage, outcome, detail, event_candidate, digest, created in events:
        if stage == "evidence_evaluation":
            try: report = json.loads(detail)
            except (TypeError, json.JSONDecodeError): report = {"status": "unavailable", "reason": "persisted evaluation report is malformed"}
        if stage in ("manifest_anchor", "static_verification", "candidate_staging", "evidence_evaluation"):
            yield_event = {"stage": stage, "outcome": outcome, "detail": detail, "candidate_id": event_candidate, "manifest_digest": digest, "created_at": created}
            yield_event["report"] = report if stage == "evidence_evaluation" else None
            # use a list without changing the persisted data
            if "history" not in locals(): history = []
            history.append(yield_event)
    return report, history if "history" in locals() else []

def evaluation_rows(report):
    """Return display data from an already persisted report, without inventing status."""
    if not isinstance(report, dict) or report.get("status") == "unavailable":
        return [("EVALUATION UNAVAILABLE", "unknown", report.get("reason", "No persisted evaluation report is available.") if isinstance(report, dict) else "No persisted evaluation report is available.", "")]
    rows = []
    for criterion in report.get("criteria", []):
        if not isinstance(criterion, dict):
            rows.append(("MALFORMED CRITERION", "unknown", "Persisted criterion data is malformed.", "")); continue
        state = criterion.get("result") if criterion.get("result") in STATE_COLORS else "unknown"
        refs = ", ".join(str(ref) for ref in criterion.get("evidence_refs", []) if ref)
        reason = criterion.get("reason") or "No explanation persisted."
        remaining = "; ".join(str(item) for item in criterion.get("remaining_evidence", []) if item)
        rows.append((criterion.get("criterion_id") or criterion.get("requirement") or "criterion", state, reason, f"Evidence: {refs or 'none'}" + (f"\nUnresolved: {remaining}" if remaining else "")))
    return rows or [("NO CRITERIA", "unknown", "The persisted report contains no criteria.", "")]

def traceability_rows(trace):
    if not isinstance(trace, list) or not trace:
        return [("TRACEABILITY UNAVAILABLE", "unavailable", "Older candidate metadata contains no requirement relationships.")]
    rows = []
    for item in trace:
        if not isinstance(item, dict) or not isinstance(item.get("requirement_id"), str) or not isinstance(item.get("requirement"), str):
            rows.append(("MALFORMED REQUIREMENT", "unavailable", "Persisted traceability data is malformed.")); continue
        coverage = item.get("coverage") if item.get("coverage") in ("full", "partial", "missing", "ambiguous") else "unavailable"
        rows.append((f"{item['requirement_id']} / {item.get('criterion_id') or 'NO CRITERION'}", coverage, item["requirement"] + "\n" + (item.get("proposal_intent") or "No proposal coverage recorded.") + "\n" + (item.get("unresolved") or "")))
    return rows

def launch(registry):
    if ft is None: raise RuntimeError("Flet is not installed; run python -m pip install flet")
    return ft.run(lambda page: MainWindow(page, registry))

class MainWindow:
    def __init__(self, page, registry):
        self.page, self.registry, self.review = page, registry, ReviewService(registry.data_dir); self.drawer_open = False; self.selected = None
        self.model_picker = ft.FilePicker(on_result=self._model_picked); self.runtime_picker = ft.FilePicker(on_result=self._runtime_picked); self.workspace_picker = ft.FilePicker(on_result=self._workspace_picked); self.workspace_path = None; page.services.extend([self.model_picker, self.runtime_picker, self.workspace_picker])
        self.model_path_field = ft.TextField(label="GGUF MODEL PATH", color=TEXT, border_color=BORDER, value=str(config_from_env().model_path or ""), expand=True)
        self.runtime_path_field = ft.TextField(label="LLAMA.CPP EXECUTABLE", color=TEXT, border_color=BORDER, value=str(config_from_env().cli_path or ""), expand=True)
        page.title = "Chimera"; page.bgcolor = BG; page.padding = 0; page.window.width = 1200; page.window.height = 760; page.window.min_width = 860; page.window.min_height = 560
        font = Path(__file__).resolve().parent.parent / "fonts" / "ec-dots-life-font" / "EcDotsLifeRegular-AydL.ttf"
        page.fonts = {"ChimeraDots": str(font)} if font.exists() else {}
        self.task_busy = False; self.model_verifying = False; self.local_verification = None; self.last_turn_notice = None; self.pending_request = None; self.pending_session_id = None; self.pending_questions = (); self.pending_state = None; self.pending_reason = ""; self.clarification_field = ft.TextField(multiline=True, min_lines=3, max_lines=6, hint_text="Answer the clarification questions...", hint_style=ft.TextStyle(color=MUTED), bgcolor=BG, color=TEXT, border_color=BORDER, focused_border_color=FOCUS, border_radius=6, expand=True); self.task_field = ft.TextField(multiline=True, min_lines=2, max_lines=5, hint_text="Message Chimera…  Describe the outcome you want...", hint_style=ft.TextStyle(color=MUTED), bgcolor=BG, color=TEXT, border_color=BORDER, focused_border_color=FOCUS, border_radius=6, expand=True); self.workspace_path_field = ft.TextField(label="WORKSPACE DIRECTORY", color=TEXT, border_color=BORDER, focused_border_color=FOCUS, value="", expand=True)
        self.agent_store = AgentSessionStore(registry.data_dir / "chimera.db")
        self.goal_store = GoalStore(registry.data_dir / "chimera.db")
        self.profile_store = ModelProfileStore(registry.data_dir / "model_profiles.json")
        self.local_provider = LocalModelProvider(config_from_env())
        self.agent_runtime = AgentRuntime(self.agent_store, self.local_provider, self.profile_store)
        self.agent_session_id = self.agent_runtime.create_session(self.workspace_path)
        self.conversation_messages = []
        self.pending_agent_approval = None; self.live_events = []
        self.live_event_count = 0
        self.agent_unsubscribe = self.agent_runtime.subscribe(self.agent_session_id, self._on_agent_event)
        try:
            pending = AuditStore(registry.data_dir / "chimera.db").clarification_pending()
            if pending:
                self.pending_session_id = pending[0]["session_id"]; self.pending_request = pending[0]["original_request"]; self.pending_questions = tuple(type("Question", (), question) for question in pending[0]["questions"]); self.pending_state = pending[0]["state"]
                current_reason = pending[0].get("recovery_reason")
                reason_text = current_reason if current_reason else "No current recovery reason is persisted (legacy or unavailable)."
                self.pending_reason = f"State: {self.pending_state}. Continuation: {pending[0]['current_run_id']}. Recovery reason: {reason_text}. Reconciliation is read-only; no retry is performed automatically." if self.pending_state != "pending" else ""
        except Exception: pass
        self.body = ft.Container(expand=True, padding=ft.Padding.symmetric(horizontal=56, vertical=36)); self.drawer = self._drawer(); self._show_overview()
        self.shell = ft.Column([self._topbar(), ft.Stack([self.body, self.drawer], expand=True), self._statusbar()], expand=True, spacing=0)
        if not self._show_intro(): page.add(self.shell)
    def _show_intro(self):
        if str(__import__("os").environ.get("CHIMERA_INTRO", "1")).lower() in ("0", "false", "off"): return False
        asset = Path(__file__).resolve().parent.parent / "assets" / "chimera_intro.gif"
        if not asset.exists(): return False
        self._intro = ft.Container(ft.Image(src=str(asset), fit=ft.BoxFit.COVER, expand=True), expand=True, bgcolor=BG)
        self.page.add(self._intro); self.page.update(); self.page.run_task(self._dismiss_intro); return True
    async def _dismiss_intro(self):
        await asyncio.sleep(2.5)
        if self._intro in self.page.controls:
            self.page.controls.clear(); self.page.add(self.shell); self.page.update()
    def _topbar(self):
        local_state = self._runtime_state().replace("RUNTIME: ", "")
        state_color = "#8EAD98" if local_state == "READY" else "#C49A6C"
        return ft.Container(
            ft.Row([
                ft.Row([
                    ft.Text("◈", color=ACCENT, size=20),
                    ft.Text("CHIMERA", color=TEXT, size=14, weight=ft.FontWeight.BOLD),
                    ft.Text("LOCAL AGENT WORKSPACE", color=MUTED, size=9),
                ], spacing=10),
                ft.Row([
                    ft.Container(
                        ft.Row([ft.Container(width=6, height=6, bgcolor=state_color, border_radius=6), ft.Text(f"LOCAL RUNTIME  /  {local_state}", color=SECONDARY, size=9)], spacing=7),
                        bgcolor=SURFACE, border=ft.Border.all(1, BORDER), border_radius=20,
                        padding=ft.Padding.symmetric(horizontal=12, vertical=7),
                    ),
                    ft.TextButton("VERIFY MODEL", on_click=self._verify_local_agent, style=ft.ButtonStyle(color=ACCENT, padding=ft.Padding.symmetric(horizontal=10, vertical=8))),
                    ft.IconButton(icon=ft.Icons.SETTINGS_OUTLINED, icon_color=SECONDARY, tooltip="Settings", on_click=self._show_settings),
                ], spacing=8),
            ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            height=58, padding=ft.Padding.symmetric(horizontal=22),
            bgcolor=BG, border=ft.Border.only(bottom=ft.BorderSide(1, BORDER)),
        )
    def _drawer(self):
        buttons = []
        buttons.append(ft.TextButton("NEW CONVERSATION", on_click=lambda e: self._new_agent_session(), style=ft.ButtonStyle(color=FOCUS, padding=ft.Padding.symmetric(horizontal=18, vertical=14))))
        for item in self.agent_store.list_sessions()[:8]:
            buttons.append(ft.TextButton(f"↳ {item['title'][:24]}", on_click=lambda e, sid=item["session_id"]: self._resume_agent_session(sid), style=ft.ButtonStyle(color=SECONDARY, padding=ft.Padding.symmetric(horizontal=18, vertical=8))))
        for label, handler in [("OVERVIEW", self._show_overview), ("MODEL HUB", self._show_model_hub), ("APPLICATIONS", self._show_computer_apps), ("WORKFLOW STUDIO", self._show_workflow_studio), ("CAPABILITIES", self._show_capabilities), ("GOALS", self._show_goals), ("RUNS", self._show_runs), ("SETTINGS", self._show_settings)]:
            buttons.append(ft.TextButton(label, on_click=lambda e, h=handler: self._navigate(h), style=ft.ButtonStyle(color=TEXT, padding=ft.Padding.symmetric(horizontal=18, vertical=14))))
        return ft.Container(ft.Column([ft.Text("NAVIGATION", color=MUTED, size=10), *buttons], spacing=3), width=210, right=18, top=10, bgcolor=SURFACE, border=ft.Border.all(1, BORDER), padding=ft.Padding.all(14), visible=False, shadow=ft.BoxShadow(blur_radius=18, color="#000000"))
    def _toggle_drawer(self, e=None): self.drawer.visible = not self.drawer.visible; self.page.update()
    def _navigate(self, handler): self.drawer.visible = False; handler(); self.page.update()
    def _runtime_state(self):
        status = check_status(config_from_env())
        return "RUNTIME: CONFIGURED" if status.state == "ready" else f"RUNTIME: {status.state.upper()}"
    def _statusbar(self):
        try: caps = len(self.registry.list()); review = len(self.review.list())
        except Exception: caps, review = "?", "?"
        return ft.Container(ft.Text(f"MODEL: {self._runtime_state().split(': ')[-1]}   ·   CAPABILITIES: {caps}   ·   REVIEW: {review}   ·   STATE: IDLE", color=MUTED, size=10), height=34, padding=ft.Padding.symmetric(horizontal=24, vertical=8), border=ft.Border.only(top=ft.BorderSide(1, BORDER)))
    def _set(self, controls): self.body.content = ft.Column(controls, expand=True, scroll=ft.ScrollMode.AUTO, horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=18); self.page.update()
    def _button(self, label, handler): return ft.FilledButton(label, on_click=handler, style=ft.ButtonStyle(bgcolor=TEXT, color=BG, overlay_color=SECONDARY))

    def _show_capabilities(self, e=None): self._set([ft.Row([ft.Text("CAPABILITIES", size=18, color=TEXT), ft.Text("/ REVIEW QUEUE", size=11, color=MUTED)], alignment=ft.MainAxisAlignment.SPACE_BETWEEN), self._candidate_list()])
    def _show_workflow_studio(self, e=None):
        service = getattr(self, "langflow_service", None) or LangflowService(); self.langflow_service = service
        status = service.status()
        if not status.configured and service.installed(): status = LangflowStatus(True, False, service.url, "Langflow installed but not started")
        if status.available:
            action = ft.TextButton("OPEN LOCAL STUDIO", on_click=lambda _: self._action(lambda: service.open_editor()), style=ft.ButtonStyle(color=FOCUS))
            detail = f"LOCAL LANGFLOW AVAILABLE\n{status.url}\nChimera validation and permissions remain authoritative."
        elif service.installed():
            action = ft.TextButton("START LANGFLOW", on_click=lambda _: self._action(lambda: service.start(), True), style=ft.ButtonStyle(color=FOCUS))
            detail = f"LANGFLOW INSTALLED / STOPPED\n{service.executable}\n{status.reason}"
        else:
            action = ft.TextButton("INSTALL LANGFLOW", on_click=lambda _: self._action(lambda: service.install(), True), style=ft.ButtonStyle(color=FOCUS))
            detail = f"LANGFLOW NOT INSTALLED\n{status.reason}\nInstallation is explicit, isolated and optional."
        self._set([ft.Text("WORKFLOW STUDIO", size=18, color=TEXT), ft.Text(detail, color=SECONDARY), action, ft.Text("Imported workflows are untrusted until Chimera validates their components, permissions and execution bounds.", color=MUTED, size=11)])
    def _show_model_hub(self, e=None):
        profiles = ModelProfileStore(self.registry.data_dir / "model_profiles.json").list()
        self.hub_id = ft.TextField(label="PROFILE ID", color=TEXT, border_color=BORDER)
        self.hub_provider = ft.TextField(label="PROVIDER ID", color=TEXT, border_color=BORDER)
        self.hub_model = ft.TextField(label="MODEL ID OR GGUF PATH", color=TEXT, border_color=BORDER)
        self.hub_url = ft.TextField(label="API BASE URL (OPTIONAL)", color=TEXT, border_color=BORDER)
        self.hub_secret = ft.TextField(label="API CREDENTIAL (NOT STORED IN PROFILE)", color=TEXT, border_color=BORDER, password=True, can_reveal_password=False)
        self.hub_tools = ft.Checkbox(label="ENABLE TOOL CALLING (E.G. WORKSPACE INSPECTION)", value=True)
        self.hub_dialect = ft.TextField(label="API DIALECT (OPTIONAL: openai_compatible, openai_responses, anthropic_messages, gemini_interactions, custom)", color=TEXT, border_color=BORDER)
        cards = [ft.Container(ft.Column([ft.Text(f"{p.display_name or p.model_id}  ·  {p.provider_id}  ·  {'API' if p.hosted else 'LOCAL'}", color=TEXT), ft.Text(f"DIALECT: {getattr(p, 'dialect', None) or 'auto'}  CAPABILITIES: {', '.join(p.modalities)}  TOOLS: {p.tool_calling}  STRUCTURED: {p.structured_output}  STATE: {'ENABLED' if p.enabled else 'DISABLED'}  EVIDENCE: {p.capability_evidence}", color=SECONDARY, size=11), ft.Row([ft.TextButton("USE", on_click=lambda e, pid=p.profile_id: self._select_session_model(pid), style=ft.ButtonStyle(color=FOCUS)), ft.TextButton("COMPAT LAB", on_click=lambda e, pid=p.profile_id: self._run_compat_lab(pid), style=ft.ButtonStyle(color=FOCUS)), ft.TextButton("DEFAULT", on_click=lambda e, pid=p.profile_id: self._set_default_model(pid), style=ft.ButtonStyle(color=FOCUS)), ft.TextButton("TOGGLE", on_click=lambda e, pid=p.profile_id: self._toggle_model(pid), style=ft.ButtonStyle(color=SECONDARY)), ft.TextButton("REMOVE", on_click=lambda e, pid=p.profile_id: self._remove_model(pid), style=ft.ButtonStyle(color=SECONDARY))])]), padding=ft.Padding.all(12), border=ft.Border.all(1, BORDER)) for p in profiles]
        form = ft.Container(ft.Column([ft.Text("ADD OR EDIT PROFILE", color=MUTED, size=10), self.hub_id, self.hub_provider, self.hub_model, self.hub_url, self.hub_secret, self.hub_dialect, self.hub_tools, ft.Row([self._button("SAVE LOCAL", lambda e: self._save_model_profile(False)), self._button("SAVE API", lambda e: self._save_model_profile(True)), ft.TextButton("TEST SELECTED API", on_click=self._test_model_profile, style=ft.ButtonStyle(color=FOCUS))])], spacing=8), bgcolor=SURFACE, padding=ft.Padding.all(14), border=ft.Border.all(1, BORDER))
        app_cards = []
        try:
            from .applications.registry import get_application_registry
            manifests = get_application_registry().discover_all()
            for m in manifests:
                actions_summary = ", ".join(f"{a.action_id} (L{a.risk_level.value})" for a in m.actions)
                app_cards.append(ft.Container(
                    ft.Column([
                        ft.Text(f"APPLICATION: {m.display_name} ({m.application_id})  ·  {'INSTALLED' if m.is_installed else 'AVAILABLE'}", color=TEXT),
                        ft.Text(f"VENDOR: {m.vendor}  ·  VERSION: {m.connector_version}  ·  PATH: {m.installation_path or 'not installed'}", color=SECONDARY, size=11),
                        ft.Text(f"ACTIONS: {actions_summary}", color=MUTED, size=10),
                    ]),
                    padding=ft.Padding.all(12),
                    border=ft.Border.all(1, BORDER),
                    bgcolor=ELEVATED,
                ))
        except Exception:
            pass
        self._set([
            ft.Text("MODEL & APPLICATION INTELLIGENCE HUB", size=18, color=TEXT),
            ft.Text("Universal model architecture & registered application ecosystem. Actions governed by Risk Levels.", color=MUTED, size=11),
            form,
            *(cards or [ft.Text("No model profiles configured yet.", color=SECONDARY)]),
            ft.Text("REGISTERED APPLICATION ECOSYSTEM", size=14, color=TEXT),
            *(app_cards or [ft.Text("No application connectors registered.", color=SECONDARY)])
        ])
    def _run_compat_lab(self, profile_id):
        try:
            profile = next((p for p in self.profile_store.list() if p.profile_id == profile_id), None)
            if not profile: raise ValueError("Profile not found")
            from .providers.registry import get_provider_registry
            from .providers.compatibility_lab import ProviderCompatibilityLab, ConformanceReportStore
            from .providers.schemas import ExecutionProvenance
            prov = get_provider_registry().resolve_from_profile(profile, credentials=CredentialStore())
            store = ConformanceReportStore(self.registry.data_dir / "conformance_reports.json")
            report = ProviderCompatibilityLab(store).run_assessment(prov, profile.model_id, provenance=ExecutionProvenance.LIVE_LOCAL if not profile.hosted else ExecutionProvenance.LIVE_REMOTE)
            self._toast(f"Lab Result: {report.certification_tier} ({report.overall_status})")
        except Exception as exc: self._toast(f"Compat Lab error: {exc}")
    def _select_session_model(self, profile_id):
        try:
            profile = next((p for p in self.profile_store.list() if p.profile_id == profile_id), None)
            local_only = not (profile and profile.hosted)
            self.agent_runtime.set_session_model(self.agent_session_id, profile_id, local_only=local_only)
            self.local_verification = None
            self._toast(f"Model {profile_id} selected ({'API / CLOUD' if profile and profile.hosted else 'LOCAL'}).")
            self._show_model_hub()
        except Exception as exc: self._toast(f"Model selection failed: {exc}")
    def _save_model_profile(self, hosted):
        try:
            from .model_hub import ModelProfile
            enable_tools = getattr(self.hub_tools, "value", True)
            dialect_val = (self.hub_dialect.value or "").strip() or None
            profile = ModelProfile(
                self.hub_id.value.strip(),
                self.hub_provider.value.strip(),
                self.hub_model.value.strip(),
                hosted,
                model_path=None if hosted else self.hub_model.value.strip(),
                base_url=self.hub_url.value.strip() or None,
                tool_calling=bool(enable_tools),
                structured_output=bool(enable_tools),
                capability_evidence="configured",
                dialect=dialect_val,
            )
            if not profile.profile_id or not profile.provider_id or not profile.model_id: raise ValueError("profile ID, provider and model are required")
            self.profile_store.save(profile)
            if hosted and self.hub_secret.value: CredentialStore().set(profile.provider_id, self.hub_secret.value)
            self.hub_secret.value = ""; self._toast("Model profile saved; provider testing remains explicit."); self._show_model_hub()
        except Exception as exc: self._toast(f"Profile save failed: {exc}")
    def _toggle_model(self, profile_id):
        try:
            profile = next(p for p in self.profile_store.list() if p.profile_id == profile_id); self.profile_store.set_enabled(profile_id, not profile.enabled); self._show_model_hub()
        except Exception as exc: self._toast(f"Profile update failed: {exc}")
    def _set_default_model(self, profile_id):
        try: self.profile_store.set_default(profile_id); self._toast("Default model updated."); self._show_model_hub()
        except Exception as exc: self._toast(f"Default model update failed: {exc}")
    def _remove_model(self, profile_id):
        def confirm(_=None):
            try:
                self.profile_store.remove(profile_id); dialog.open = False; self.page.update(); self._toast("Model profile removed."); self._show_model_hub()
            except Exception as exc: self._toast(f"Profile removal failed: {exc}")
        dialog = ft.AlertDialog(modal=True, title=ft.Text("REMOVE MODEL PROFILE?"), content=ft.Text(f"This removes profile {profile_id} from Model Hub. Stored credentials are not automatically deleted."), actions=[ft.TextButton("CANCEL", on_click=lambda e: (setattr(dialog, "open", False), self.page.update())), ft.TextButton("REMOVE", on_click=confirm)])
        self.page.show_dialog(dialog)
    async def _test_model_profile(self, e=None):
        try:
            profile = next((p for p in self.profile_store.list() if p.profile_id == (self.hub_id.value or "").strip()), None)
            if not profile or not profile.hosted: raise ValueError("select or enter a hosted API profile first")
            from .providers.registry import get_provider_registry
            provider = get_provider_registry().resolve_from_profile(profile, credentials=CredentialStore())
            from .providers.schemas import GenerationRequest, NormalizedMessage
            req = GenerationRequest(model=profile.model_id, messages=(NormalizedMessage(role="user", content="Reply with the word test."),))
            resp = await asyncio.to_thread(provider.generate, req)
            self._toast(f"Provider ready: {resp.provider_id} / {resp.model} ({resp.usage.duration_ms} ms)")
        except Exception as exc: self._toast(f"Provider test failed: {exc}")
    def _candidate_list(self):
        try: candidates = self.review.list()
        except Exception as exc: return ft.Text(str(exc), color=TEXT)
        if not candidates: return ft.Text("No persisted candidates.", color=MUTED)
        return ft.Column([ft.Container(ft.Row([ft.Column([ft.Text(c.name, color=TEXT), ft.Text(c.candidate_id, color=MUTED, size=10)], expand=True), ft.Text(_upper(c.status), color=SECONDARY, size=10), ft.TextButton("INSPECT", on_click=lambda e, c=c: self._inspect(c), style=ft.ButtonStyle(color=TEXT))]), padding=ft.Padding.symmetric(vertical=12), border=ft.Border.only(bottom=ft.BorderSide(1, BORDER))) for c in candidates], spacing=0)
    def _inspect(self, c):
        self.selected = c; self._set([ft.Text(f"CANDIDATE / {c.candidate_id}", size=18, color=TEXT), ft.Text(f"{c.name}\nSTATE: {c.status}\nTASK: {c.task}\nMANIFEST: {c.digest or 'UNVERIFIED'}\nACCEPTANCE APPROVAL: {c.acceptance_approved}\n\nStatic verification is not behavioral verification. Candidate code is never executed during review.", color=SECONDARY), ft.Row([ft.TextButton("VERIFY", on_click=lambda e: self._action(lambda: self.review.verify(c.candidate_id)), style=ft.ButtonStyle(color=TEXT)), ft.TextButton("APPROVE ACCEPTANCE", on_click=lambda e: self._action(lambda: self.review.approve_acceptance(c.candidate_id)), style=ft.ButtonStyle(color=TEXT)), ft.TextButton("ACCEPT", on_click=lambda e: self._action(lambda: self.review.accept(c.candidate_id), True), style=ft.ButtonStyle(color=TEXT))])])
    def _action(self, action, refresh=False):
        try: action(); self._toast("Completed through ReviewService."); self._show_capabilities() if refresh else None
        except Exception as exc: self._toast(f"Action failed: {exc}")
    def _show_computer_apps(self, e=None):
        """Operator-controlled process authorization for the active conversation."""
        try:
            applications = self.agent_runtime.computer.backend.list_applications()
        except Exception as exc:
            return self._set([
                ft.Text("APPLICATION AUTHORIZATION", size=22, color=TEXT),
                ft.Text(f"Application inventory unavailable: {exc}", color=SECONDARY),
            ])

        cards = []
        for application in applications[:80]:
            pid = application.get("pid")
            executable = application.get("executable")
            titles = ", ".join(
                window.get("title") or window.get("class_name") or "Untitled window"
                for window in application.get("windows", [])[:3]
            )
            authorized = any(
                identity.owner_session_id == self.agent_session_id
                and identity.pid == pid
                and (identity.executable or "").casefold() == (executable or "").casefold()
                and self.agent_runtime.computer._identity_is_current(identity)
                for identity in self.agent_runtime.computer.sessions.values()
            )
            action = (
                ft.Text("AUTHORIZED FOR THIS CONVERSATION", color=FOCUS, size=10)
                if authorized
                else self._button(
                    "AUTHORIZE THIS APPLICATION",
                    lambda e, process_id=pid, image=executable: self._authorize_computer_application(process_id, image),
                )
            )
            cards.append(ft.Container(
                ft.Column([
                    ft.Text(str(executable or "Executable path unavailable"), color=TEXT, weight=ft.FontWeight.BOLD, size=12),
                    ft.Text(f"PID {pid} · {titles or 'No visible title'}", color=SECONDARY, size=10),
                    action,
                ], spacing=8),
                bgcolor=SURFACE,
                border=ft.Border.all(1, BORDER),
                border_radius=7,
                padding=ft.Padding.all(14),
                width=760,
            ))

        self._set([
            ft.Text("APPLICATION AUTHORIZATION", size=22, color=TEXT),
            ft.Text(
                "Choose each application explicitly before Chimera can inspect its controls. "
                "Authorization is scoped to this conversation. Global keyboard input, blind focus changes "
                "and remote window closing are disabled.",
                color=SECONDARY,
                size=11,
            ),
            *(cards or [ft.Text("No visible applications were found.", color=MUTED)]),
        ])

    def _authorize_computer_application(self, pid, executable):
        try:
            identity = self.agent_runtime.computer.create(
                pid,
                executable,
                owner_session_id=self.agent_session_id,
            )
            self._toast(f"Authorized {Path(identity.executable).name} for this conversation.")
            self._show_computer_apps()
        except Exception as exc:
            self._toast(f"Application authorization failed: {exc}")

    def _show_runs(self, e=None): self._set([ft.Text("RUNS", size=18, color=TEXT), ft.Text("No executions recorded.", color=MUTED)])
    def _show_settings(self, e=None):
        c = config_from_env(); s = check_status(c); source = config_source(); policy = load_policy(self.registry.data_dir); server = self.local_provider.server.status() if self.local_provider.server else {"state": "unavailable"}
        self._set([ft.Text("SETTINGS", size=18, color=TEXT), ft.Text(f"LOCAL RUNTIME: {_upper(s.state)}\nSERVER: {_upper(server['state'])}\nMODEL: {c.model_path or 'NOT CONFIGURED'} ({source['model']})\nSERVER EXECUTABLE: {c.server_path or 'NOT AVAILABLE'}\nSERVER PID: {server.get('pid') or 'none'}  PORT: {server.get('url') or 'none'}\nCONTEXT: {c.context}  TOKENS: {c.max_tokens}  THREADS: {c.threads}  GPU LAYERS: {c.gpu_layers}\nPOLICY: {policy['mode']}  IDLE TIMEOUT: {policy['idle_timeout']}s\n\nDATA: {self.registry.data_dir}\nEnvironment variables override persisted model_config.json.", color=SECONDARY), ft.Row([self.model_path_field, ft.TextButton("SELECT GGUF", on_click=lambda e: self.model_picker.pick_files(allowed_extensions=["gguf"]))]), ft.Row([self.runtime_path_field, ft.TextButton("SELECT CLI RUNTIME", on_click=lambda e: self.runtime_picker.pick_files())]), ft.Row([self._button("SAVE + VALIDATE", self._save_model_config), ft.TextButton("VERIFY AGENT CHAT", on_click=self._verify_local_agent, style=ft.ButtonStyle(color=ACCENT)), ft.TextButton("START SERVER", on_click=self._start_local_server, style=ft.ButtonStyle(color=TEXT)), ft.TextButton("STOP SERVER", on_click=self._stop_local_server, style=ft.ButtonStyle(color=TEXT))]), ft.Text("A successful protocol verification runs a real local structured inference. A configured file alone is not treated as proof that the model works. The managed server is loopback-only; no model download or hosted fallback is used.", color=MUTED, size=11)])
    def _start_local_server(self, e=None):
        try: self.local_provider.server.start(); self._toast("Local model server is ready."); self._show_settings()
        except Exception as exc: self._toast(f"Server start failed: {exc}")
    def _stop_local_server(self, e=None):
        self.local_provider.stop(); self._toast("Local model server stopped."); self._show_settings()
    def _model_picked(self, e):
        if e.files: self.model_path_field.value = e.files[0].path; self.page.update()
    def _runtime_picked(self, e):
        if e.files: self.runtime_path_field.value = e.files[0].path; self.page.update()
    def _save_model_config(self, e=None):
        model = Path(self.model_path_field.value) if self.model_path_field.value else None; runtime = self.runtime_path_field.value or None
        if model and model.suffix.lower() != ".gguf": return self._toast("Model must be an existing .gguf file.")
        if model and not model.is_file(): return self._toast("Selected model file does not exist.")
        if runtime and not Path(runtime).is_file(): return self._toast("Selected llama.cpp executable does not exist.")
        save_persisted_config(model, runtime)
        try:
            self.local_provider.stop()
        except Exception:
            pass
        self.local_provider = LocalModelProvider(config_from_env())
        self.agent_runtime.provider = self.local_provider
        self.local_verification = None
        self._toast(f"Saved. Provider state: {_upper(check_status(config_from_env()).state)}")
        self._show_settings()
    async def _test_local_inference(self, e=None):
        self._toast("Running genuine local inference..."); result = await asyncio.to_thread(LocalModelProvider(config_from_env()).plan, "Return a concise JSON TaskSpec for creating a deterministic local text utility.")
        self._toast(f"Inference {result.status}: {result.duration_ms} ms, {result.generated_tokens} generated tokens" if result.status == "ready" else f"Inference failed: {result.error or result.status}")
    def _toast(self, message): self.page.snack_bar = ft.SnackBar(ft.Text(message, color=TEXT), bgcolor=ELEVATED); self.page.snack_bar.open = True; self.page.update()
    def _show_runs(self, e=None):
        try: runs = AuditStore(self.registry.data_dir / "chimera.db").workflow_runs()
        except Exception as exc: return self._set([ft.Text("RUN HISTORY", size=22, color=TEXT), ft.Text(f"History unavailable: {exc}", color=SECONDARY)])
        execution_rows = []
        for record in sorted((self.registry.data_dir / "executions").glob("*/execution.json")) if (self.registry.data_dir / "executions").is_dir() else []:
            try:
                item = json.loads(record.read_text(encoding="utf-8")); execution_rows.append(item)
            except (OSError, json.JSONDecodeError):
                execution_rows.append({"status": "unavailable", "error": f"Malformed execution record: {record.name}"})
        if not runs and not execution_rows: return self._set([ft.Text("RUN HISTORY", size=22, color=TEXT), ft.Text("No workflow or execution runs recorded yet.", color=MUTED)])
        cards = []
        for run_id, summary, started, finished, status, candidate, digest, error in runs:
            status_text = str(status or "unknown")
            detail = f"{summary}\n\nSTARTED  {started}\nSTATUS  {_upper(status_text)}\nCANDIDATE  {candidate or '—'}\nDIGEST  {digest or '—'}" + (f"\nERROR  {error}" if error else "")
            cards.append(ft.Container(ft.Column([ft.Row([ft.Text(run_id[:12], color=TEXT, weight=ft.FontWeight.BOLD), ft.Text(_upper(status_text), color=FOCUS if status_text == "pending_review" else SECONDARY, size=10)], alignment=ft.MainAxisAlignment.SPACE_BETWEEN), ft.Text(detail, color=SECONDARY, size=11)]), bgcolor=SURFACE, border=ft.Border.all(1, BORDER), border_radius=7, padding=ft.Padding.all(16), width=780))
        for item in execution_rows:
            status = _upper(item.get("status")); artifact = item.get("artifact") or "none"; error = item.get("error") or "none"
            cards.append(ft.Container(ft.Column([ft.Row([ft.Text(str(item.get("task_id", "execution"))[:12], color=TEXT, weight=ft.FontWeight.BOLD), ft.Text(status, color=FOCUS if status == "COMPLETED" else SECONDARY, size=10)], alignment=ft.MainAxisAlignment.SPACE_BETWEEN), ft.Text(f"{item.get('objective', 'Unknown objective')}\nARTIFACT  {artifact}\nERROR  {error}", color=SECONDARY, size=11)]), bgcolor=SURFACE, border=ft.Border.all(1, BORDER), border_radius=7, padding=ft.Padding.all(16), width=780))
        self._set([ft.Text("RUN HISTORY", size=22, color=TEXT), ft.Text("Persisted workflow events. This view never fabricates activity.", color=MUTED, size=11), *cards])

    def _show_goals(self, e=None):
        """Render the operator-owned goal ledger, separate from individual task runs."""
        try:
            stats = self.goal_store.stats()
            goals = self.goal_store.list(limit=200)
        except Exception as exc:
            return self._set([
                ft.Text("GOALS & AMBITIONS", size=22, color=TEXT),
                ft.Text(f"Goal history unavailable: {exc}", color=SECONDARY),
            ])

        self.goal_title_field = ft.TextField(
            label="GOAL / DESIRED OUTCOME", color=TEXT, border_color=BORDER,
            focused_border_color=FOCUS, hint_text="What do you ultimately want to achieve?",
        )
        self.goal_description_field = ft.TextField(
            label="DESCRIPTION / CONTEXT", color=TEXT, border_color=BORDER,
            focused_border_color=FOCUS, multiline=True, min_lines=2, max_lines=4,
            hint_text="Why does this matter? What constraints should Chimera remember?",
        )
        self.goal_criteria_field = ft.TextField(
            label="SUCCESS CRITERIA (ONE PER LINE)", color=TEXT, border_color=BORDER,
            focused_border_color=FOCUS, multiline=True, min_lines=2, max_lines=4,
            hint_text="What evidence would demonstrate progress or completion?",
        )
        self.goal_project_field = ft.TextField(
            label="PROJECT / AREA (OPTIONAL)", color=TEXT, border_color=BORDER,
            focused_border_color=FOCUS, hint_text="e.g. Chimera",
        )
        self.goal_priority_field = ft.TextField(
            label="PRIORITY", value="normal", color=TEXT, border_color=BORDER,
            focused_border_color=FOCUS, hint_text="low / normal / high / critical",
        )

        summary = ft.Row([
            self._goal_stat("ACTIVE", stats.get("active", 0)),
            self._goal_stat("PAUSED", stats.get("paused", 0)),
            self._goal_stat("COMPLETED", stats.get("completed", 0)),
            self._goal_stat("ARCHIVED", stats.get("archived", 0)),
        ], wrap=True, spacing=10)

        form = ft.Container(
            ft.Column([
                ft.Text("CAPTURE AN AMBITION", color=ACCENT, size=10, weight=ft.FontWeight.BOLD),
                self.goal_title_field,
                self.goal_description_field,
                self.goal_criteria_field,
                ft.Row([self.goal_project_field, self.goal_priority_field], wrap=True),
                ft.Row([
                    self._button("SAVE GOAL", self._create_goal),
                    ft.Text("Goals remain active until the operator changes their state.", color=MUTED, size=10),
                ], wrap=True, spacing=12),
            ], spacing=10),
            bgcolor=SURFACE, border=ft.Border.all(1, BORDER), border_radius=9,
            padding=ft.Padding.all(18), width=820,
        )

        cards = [self._goal_card(goal) for goal in goals]
        if not cards:
            cards = [ft.Container(
                ft.Text("No goals recorded yet. Add an outcome above; Chimera will keep it separate from chat sessions and task runs.", color=MUTED),
                bgcolor=SURFACE, border=ft.Border.all(1, BORDER), border_radius=8,
                padding=ft.Padding.all(18), width=820,
            )]

        self._set([
            ft.Row([
                ft.Column([
                    ft.Text("GOALS & AMBITIONS", size=22, color=TEXT),
                    ft.Text("Long-term outcomes, project milestones and completion evidence.", color=MUTED, size=11),
                ], spacing=4),
                ft.Text(f"{stats.get('total', 0)} RECORDED", size=10, color=ACCENT),
            ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            summary,
            form,
            ft.Text("YOUR GOALS", color=SECONDARY, size=11, weight=ft.FontWeight.BOLD),
            *cards,
        ])

    def _goal_stat(self, label, value):
        return ft.Container(
            ft.Column([
                ft.Text(str(value), size=20, color=TEXT, weight=ft.FontWeight.BOLD),
                ft.Text(label, size=9, color=MUTED),
            ], spacing=2),
            bgcolor=SURFACE, border=ft.Border.all(1, BORDER), border_radius=8,
            padding=ft.Padding.symmetric(horizontal=18, vertical=10),
        )

    def _create_goal(self, e=None):
        try:
            criteria = [
                item.strip() for item in (self.goal_criteria_field.value or "").splitlines()
                if item.strip()
            ]
            tags = []
            goal = self.goal_store.create(
                self.goal_title_field.value or "",
                self.goal_description_field.value or "",
                priority=(self.goal_priority_field.value or "normal").strip().lower(),
                project=self.goal_project_field.value or "",
                success_criteria=criteria,
                tags=tags,
            )
            self._toast(f"Goal saved: {goal.title}")
            self._show_goals()
        except Exception as exc:
            self._toast(f"Goal could not be saved: {exc}")

    def _goal_card(self, goal):
        state_color = {
            "active": "#8EAD98",
            "paused": "#C49A6C",
            "completed": "#8EAD98",
            "archived": MUTED,
        }.get(goal.status, SECONDARY)
        detail = goal.description or "No additional context recorded."
        if goal.project:
            detail += f"\nPROJECT  {goal.project}"
        if goal.success_criteria:
            detail += "\nSUCCESS CRITERIA\n" + "\n".join(f"• {item}" for item in goal.success_criteria)
        if goal.evidence:
            detail += "\nCOMPLETION EVIDENCE\n" + "\n".join(f"• {item}" for item in goal.evidence)
        if goal.completion_note:
            detail += f"\nCOMPLETION NOTE  {goal.completion_note}"

        actions = []
        if goal.status == "active":
            actions.extend([
                ft.TextButton("PAUSE", on_click=lambda e, gid=goal.goal_id: self._goal_action(gid, "paused"), style=ft.ButtonStyle(color=SECONDARY)),
                ft.TextButton("COMPLETE", on_click=lambda e, gid=goal.goal_id: self._goal_action(gid, "completed"), style=ft.ButtonStyle(color=ACCENT)),
                ft.TextButton("ARCHIVE", on_click=lambda e, gid=goal.goal_id: self._goal_action(gid, "archived"), style=ft.ButtonStyle(color=MUTED)),
            ])
        elif goal.status == "paused":
            actions.extend([
                ft.TextButton("RESUME", on_click=lambda e, gid=goal.goal_id: self._goal_action(gid, "active"), style=ft.ButtonStyle(color=SECONDARY)),
                ft.TextButton("COMPLETE", on_click=lambda e, gid=goal.goal_id: self._goal_action(gid, "completed"), style=ft.ButtonStyle(color=ACCENT)),
                ft.TextButton("ARCHIVE", on_click=lambda e, gid=goal.goal_id: self._goal_action(gid, "archived"), style=ft.ButtonStyle(color=MUTED)),
            ])
        elif goal.status == "completed":
            actions.extend([
                ft.TextButton("REOPEN", on_click=lambda e, gid=goal.goal_id: self._goal_action(gid, "active"), style=ft.ButtonStyle(color=SECONDARY)),
                ft.TextButton("ARCHIVE", on_click=lambda e, gid=goal.goal_id: self._goal_action(gid, "archived"), style=ft.ButtonStyle(color=MUTED)),
            ])
        else:
            actions.append(ft.TextButton(
                "RESTORE", on_click=lambda e, gid=goal.goal_id: self._goal_action(gid, "active"),
                style=ft.ButtonStyle(color=SECONDARY),
            ))

        return ft.Container(
            ft.Column([
                ft.Row([
                    ft.Column([
                        ft.Text(goal.title, color=TEXT, size=14, weight=ft.FontWeight.BOLD),
                        ft.Text(f"{goal.priority.upper()} PRIORITY  ·  {goal.status.upper()}", color=state_color, size=9),
                    ], spacing=4, expand=True),
                    ft.Text(goal.goal_id[:10], color=MUTED, size=9),
                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                ft.Text(detail, color=SECONDARY, size=11),
                ft.Row(actions, wrap=True, spacing=4),
                ft.Text(f"UPDATED  {goal.updated_at[:19].replace('T', ' ')} UTC", color=MUTED, size=8),
            ], spacing=9),
            bgcolor=SURFACE, border=ft.Border.all(1, BORDER), border_radius=8,
            padding=ft.Padding.all(16), width=820,
        )

    def _goal_action(self, goal_id, status):
        goal = self.goal_store.get(goal_id)
        if goal is None:
            self._toast("Goal no longer exists.")
            self._show_goals()
            return

        needs_evidence = status == "completed"
        needs_reason = status == "active" and goal.status in {"completed", "archived"}
        if needs_evidence or needs_reason:
            evidence_field = ft.TextField(
                label="COMPLETION EVIDENCE (ONE REFERENCE PER LINE)" if needs_evidence else "WHY IS THIS GOAL BEING REOPENED?",
                multiline=True, min_lines=2, max_lines=5, color=TEXT,
                border_color=BORDER, focused_border_color=FOCUS,
                hint_text="Artifact paths, run IDs, test results or URLs" if needs_evidence else "Describe what changed or why the goal is active again.",
            )
            note_field = ft.TextField(
                label="COMPLETION NOTE (OPTIONAL)" if needs_evidence else "ADDITIONAL CONTEXT (OPTIONAL)",
                multiline=True, min_lines=1, max_lines=3, color=TEXT,
                border_color=BORDER, focused_border_color=FOCUS,
            )

            def confirm(_=None):
                try:
                    evidence = [
                        line.strip() for line in (evidence_field.value or "").splitlines()
                        if line.strip()
                    ] if needs_evidence else None
                    reason = (note_field.value or "").strip() if needs_evidence else (evidence_field.value or "").strip()
                    if needs_evidence and not reason:
                        reason = None
                    self.goal_store.transition(
                        goal_id, status, reason=reason, evidence=evidence,
                    )
                    dialog.open = False
                    self.page.update()
                    self._toast("Goal state and evidence saved.")
                    self._show_goals()
                except Exception as exc:
                    self._toast(f"Goal update failed: {exc}")

            dialog = ft.AlertDialog(
                modal=True,
                title=ft.Text("RECORD GOAL COMPLETION" if needs_evidence else "REOPEN GOAL"),
                content=ft.Column([evidence_field, note_field], tight=True, spacing=10),
                actions=[
                    ft.TextButton("CANCEL", on_click=lambda e: (setattr(dialog, "open", False), self.page.update())),
                    ft.TextButton("CONFIRM", on_click=confirm, style=ft.ButtonStyle(color=ACCENT)),
                ],
            )
            self.page.show_dialog(dialog)
            return

        try:
            self.goal_store.transition(
                goal_id, status,
                reason=f"Operator changed goal state from {goal.status} to {status}.",
            )
            self._toast(f"Goal moved to {status}.")
            self._show_goals()
        except Exception as exc:
            self._toast(f"Goal update failed: {exc}")

    def _nav_item(self, glyph, label, handler, active=False):
        return ft.Container(
            ft.Row([
                ft.Text(glyph, color=ACCENT if active else MUTED, size=15, width=22),
                ft.Text(label, color=TEXT if active else SECONDARY, size=11, weight=ft.FontWeight.BOLD if active else ft.FontWeight.NORMAL),
            ], spacing=9),
            bgcolor=ELEVATED if active else None,
            border=ft.Border.all(1, BORDER) if active else None,
            border_radius=8,
            padding=ft.Padding.symmetric(horizontal=12, vertical=11),
            on_click=lambda e: self._navigate(handler),
        )

    def _session_title(self, session_id):
        try:
            for message in self.agent_store.messages(session_id):
                if message.role == "user":
                    return " ".join(message.content.split())[:34] or "New conversation"
        except Exception:
            pass
        return "New conversation"

    def _insert_prompt(self, prompt):
        self.task_field.value = prompt
        self.task_field.focus()
        self.page.update()

    def _chat_sidebar(self):
        sessions = self.agent_store.list_sessions(limit=12)
        recent = []
        for item in sessions:
            sid = item["session_id"]
            label = self._session_title(sid)
            is_current = sid == self.agent_session_id
            recent.append(
                ft.Container(
                    ft.Row([
                        ft.Text("●", color=ACCENT if is_current else MUTED, size=8),
                        ft.Text(label, color=TEXT if is_current else SECONDARY, size=10),
                    ], spacing=9),
                    bgcolor=ELEVATED if is_current else None,
                    border=ft.Border.all(1, BORDER) if is_current else None,
                    border_radius=7,
                    padding=ft.Padding.symmetric(horizontal=9, vertical=9),
                    on_click=lambda e, session_id=sid: self._resume_agent_session(session_id),
                )
            )

        try:
            config = config_from_env()
            model_name = Path(config.model_path).name if config.model_path else "No GGUF selected"
        except Exception:
            model_name = "Model configuration unavailable"

        verify_label = "NOT VERIFIED"
        verify_color = MUTED
        if self.local_verification:
            verify_label = "AGENT TEST PASSED"
            verify_color = "#8EAD98"

        return ft.Container(
            ft.Column([
                ft.Row([ft.Text("◈", color=ACCENT, size=20), ft.Text("CHIMERA", color=TEXT, size=13, weight=ft.FontWeight.BOLD)], spacing=9),
                ft.Container(
                    self._button("＋   NEW CHAT", lambda e: self._new_agent_session()),
                    width=220,
                ),
                ft.Text("WORKSPACE", color=MUTED, size=9, weight=ft.FontWeight.BOLD),
                self._nav_item("⌂", "Chat", self._show_overview, True),
                self._nav_item("▤", "Model hub", self._show_model_hub),
                self._nav_item("▣", "Applications", self._show_computer_apps),
                self._nav_item("◇", "Workflow studio", self._show_workflow_studio),
                self._nav_item("⬡", "Capabilities", self._show_capabilities),
                self._nav_item("◷", "Run history", self._show_runs),
                ft.Divider(height=1, color=BORDER),
                ft.Row([ft.Text("RECENT CONVERSATIONS", color=MUTED, size=9, weight=ft.FontWeight.BOLD), ft.TextButton("＋", on_click=lambda e: self._new_agent_session(), style=ft.ButtonStyle(color=ACCENT))], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                ft.Column(recent or [ft.Text("Your conversations will appear here.", color=MUTED, size=10)], spacing=3, scroll=ft.ScrollMode.AUTO, expand=True),
                ft.Container(
                    ft.Column([
                        ft.Text("LOCAL MODEL", color=MUTED, size=9, weight=ft.FontWeight.BOLD),
                        ft.Text(model_name, color=TEXT, size=10),
                        ft.Row([ft.Container(width=6, height=6, bgcolor=verify_color, border_radius=6), ft.Text(verify_label, color=verify_color, size=8)], spacing=7),
                        ft.TextButton("Configure runtime  ↗", on_click=self._show_settings, style=ft.ButtonStyle(color=ACCENT, padding=ft.Padding.all(0))),
                    ], spacing=6),
                    bgcolor=SURFACE, border=ft.Border.all(1, BORDER), border_radius=9, padding=ft.Padding.all(12),
                ),
            ], spacing=10, expand=True),
            width=246, bgcolor="#0E1210", padding=ft.Padding.all(14),
            border=ft.Border.only(right=ft.BorderSide(1, BORDER)),
        )

    def _chat_bubble(self, role, content, created_at=None):
        if role == "user":
            bubble = ft.Container(
                ft.Column([
                    ft.Text("YOU", color=ACCENT, size=8, weight=ft.FontWeight.BOLD),
                    ft.Text(content, color=TEXT, size=13),
                ], spacing=7),
                bgcolor="#202722", border=ft.Border.all(1, "#344139"),
                border_radius=12, padding=ft.Padding.all(15), width=720,
            )
            return ft.Row([bubble], alignment=ft.MainAxisAlignment.END)

        bubble = ft.Container(
            ft.Column([
                ft.Row([
                    ft.Text("◈", color=ACCENT, size=15),
                    ft.Text("CHIMERA", color=TEXT, size=9, weight=ft.FontWeight.BOLD),
                    ft.Text("LOCAL AGENT", color=MUTED, size=8),
                ], spacing=7),
                ft.Text(content, color=SECONDARY, size=13),
            ], spacing=9),
            bgcolor=SURFACE, border=ft.Border.all(1, BORDER),
            border_radius=12, padding=ft.Padding.all(16), width=760,
        )
        return ft.Row([bubble], alignment=ft.MainAxisAlignment.START)

    def _tool_card(self, content):
        try:
            payload = json.loads(content)
            tool = payload.get("tool") or "Tool operation"
            status = str(payload.get("status") or "recorded").upper()
            result = payload.get("result")
            detail = json.dumps(result, ensure_ascii=False, indent=2, default=str)[:1400] if result is not None else (payload.get("error") or "")
        except Exception:
            tool, status, detail = "Tool event", "RECORDED", content[:1000]
        color = "#8EAD98" if status == "COMPLETED" else "#C49A6C" if status in {"AWAITING_APPROVAL", "RECORDED"} else "#D9897A"
        return ft.Container(
            ft.Column([
                ft.Row([
                    ft.Text("⌁", color=ACCENT, size=14),
                    ft.Text(str(tool), color=TEXT, size=10, weight=ft.FontWeight.BOLD),
                    ft.Container(expand=True),
                    ft.Text(status, color=color, size=8, weight=ft.FontWeight.BOLD),
                ]),
                ft.Text(detail or "No additional output.", color=MUTED, size=10),
            ], spacing=7),
            bgcolor="#101512", border=ft.Border.all(1, BORDER),
            border_radius=8, padding=ft.Padding.all(12), width=700,
        )

    def _conversation_content(self):
        content = []
        try:
            messages = self.agent_store.messages(self.agent_session_id)
        except Exception:
            messages = []
        stored_pairs = set()
        for message in messages:
            if message.role in {"user", "assistant"}:
                stored_pairs.add((message.role, message.content))
                content.append(self._chat_bubble(message.role, message.content, message.created_at))
            elif message.role == "tool":
                content.append(self._tool_card(message.content))

        for role, value in self.conversation_messages:
            if role in {"user", "assistant"} and (role, value) not in stored_pairs:
                content.append(self._chat_bubble(role, value))

        if not content:
            suggestions = [
                ("Inspect a project", "Inspect the current project workspace and summarize its structure."),
                ("Explain my codebase", "Explain the architecture of the current project. Start by inspecting the workspace."),
                ("Build something", "Help me plan and build a small, testable utility in the current workspace."),
            ]
            chips = [
                ft.OutlinedButton(label, on_click=lambda e, prompt=prompt: self._insert_prompt(prompt), style=ft.ButtonStyle(color=SECONDARY, side=ft.BorderSide(1, BORDER), padding=ft.Padding.symmetric(horizontal=13, vertical=10)))
                for label, prompt in suggestions
            ]
            content.append(
                ft.Container(
                    ft.Column([
                        ft.Text("◈", color=ACCENT, size=40),
                        ft.Text("What are we working on?", color=TEXT, size=27, weight=ft.FontWeight.BOLD),
                        ft.Text("A local-first agent for thinking, building and using your tools.", color=SECONDARY, size=12),
                        ft.Row(chips, wrap=True, spacing=8),
                    ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=16),
                    alignment=ft.Alignment(0, 0), expand=True,
                    padding=ft.Padding.symmetric(horizontal=20, vertical=45),
                )
            )

        if self.task_busy:
            live = []
            for event_type, payload in self.live_events[-8:]:
                if event_type.startswith("model.request"):
                    label = "Local model is reasoning"
                elif event_type == "tool.call.requested":
                    label = f"Preparing {payload.get('tool', 'tool operation')}"
                elif event_type == "tool.started":
                    label = f"Running {payload.get('tool', 'tool operation')}"
                elif event_type == "tool.completed":
                    label = f"Completed {payload.get('tool', 'tool operation')}"
                elif event_type == "tool.failed":
                    label = f"Could not complete {payload.get('tool', 'tool operation')}"
                else:
                    continue
                live.append(ft.Text(f"•  {label}", color=SECONDARY, size=10))
            content.append(
                ft.Container(
                    ft.Row([
                        ft.ProgressRing(width=15, height=15, stroke_width=2, color=ACCENT),
                        ft.Column([
                            ft.Text("CHIMERA IS WORKING", color=ACCENT, size=8, weight=ft.FontWeight.BOLD),
                            ft.Text(live[-1].value if live else "Sending the request to your local model…", color=SECONDARY, size=11),
                        ], spacing=4),
                    ], spacing=12),
                    bgcolor=ELEVATED, border=ft.Border.all(1, BORDER),
                    border_radius=9, padding=ft.Padding.all(13), width=700,
                )
            )

        if self.last_turn_notice:
            content.append(
                ft.Container(
                    ft.Text(self.last_turn_notice, color="#D9A08F", size=11),
                    bgcolor="#261A18", border=ft.Border.all(1, "#684039"),
                    border_radius=8, padding=ft.Padding.all(12), width=700,
                )
            )

        if self.pending_agent_approval:
            pending = self.pending_agent_approval
            content.append(
                ft.Container(
                    ft.Column([
                        ft.Row([ft.Text("OPERATOR APPROVAL REQUIRED", color=ACCENT, size=9, weight=ft.FontWeight.BOLD), ft.Container(expand=True), ft.Text("PAUSED", color=MUTED, size=8)]),
                        ft.Text(str(pending.get("tool", "Protected operation")), color=TEXT, size=12, weight=ft.FontWeight.BOLD),
                        ft.Text(json.dumps(pending.get("arguments", {}), ensure_ascii=False, indent=2), color=SECONDARY, size=10),
                        ft.Text("Review the exact operation before allowing it to run.", color=MUTED, size=10),
                        ft.Row([
                            self._button("APPROVE THIS ACTION", lambda e: self._approve_agent_tool(True)),
                            ft.TextButton("Reject", on_click=lambda e: self._approve_agent_tool(False), style=ft.ButtonStyle(color=SECONDARY)),
                        ], spacing=10),
                    ], spacing=9),
                    bgcolor="#211D16", border=ft.Border.all(1, ACCENT),
                    border_radius=10, padding=ft.Padding.all(15), width=720,
                )
            )
        return content

    def _show_overview(self, e=None):
        session = self.agent_store.session(self.agent_session_id) or {}
        session_mode = session.get("mode", "plan")
        session_title = self._session_title(self.agent_session_id)
        model_info = self.agent_store.session_model(self.agent_session_id)
        selected_model = model_info.get("profile_id") or (Path(session.get("model")).name if session.get("model") else "Default local GGUF")
        workspace_label = str(self.workspace_path) if self.workspace_path else "No workspace attached"

        if not hasattr(self, "task_field") or self.task_field is None:
            self.task_field = ft.TextField(multiline=True, min_lines=2, max_lines=5, hint_text="Message Chimera…  (describe the outcome, not just the steps)", hint_style=ft.TextStyle(color=MUTED), bgcolor="#0D110F", color=TEXT, border_color=BORDER, focused_border_color=ACCENT, border_radius=8, expand=True)
        self.task_field.disabled = self.task_busy

        header = ft.Container(
            ft.Row([
                ft.Column([
                    ft.Text("CONVERSATION", color=MUTED, size=8, weight=ft.FontWeight.BOLD),
                    ft.Text(session_title, color=TEXT, size=17, weight=ft.FontWeight.BOLD),
                    ft.Text(workspace_label, color=MUTED, size=9),
                ], spacing=4, expand=True),
                ft.Row([
                    ft.Container(ft.Text(f"{session_mode.upper()} MODE", color=ACCENT, size=8, weight=ft.FontWeight.BOLD), bgcolor=ELEVATED, border=ft.Border.all(1, BORDER), border_radius=20, padding=ft.Padding.symmetric(horizontal=11, vertical=7)),
                    ft.TextButton("SWITCH TO BUILD" if session_mode == "plan" else "SWITCH TO PLAN", on_click=self._toggle_agent_mode, disabled=self.task_busy, style=ft.ButtonStyle(color=ACCENT)),
                ], spacing=6),
            ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            height=82, padding=ft.Padding.symmetric(horizontal=28, vertical=14),
            border=ft.Border.only(bottom=ft.BorderSide(1, BORDER)), bgcolor=BG,
        )

        composer = ft.Container(
            ft.Column([
                ft.Row([
                    ft.Text("MESSAGE", color=MUTED, size=8, weight=ft.FontWeight.BOLD),
                    ft.Container(expand=True),
                    ft.Text("LOCAL ONLY  ·  PERMISSION-GATED TOOLS", color="#8EAD98", size=8),
                    ft.TextButton("ATTACH WORKSPACE", on_click=self._pick_workspace, style=ft.ButtonStyle(color=ACCENT, padding=ft.Padding.symmetric(horizontal=7, vertical=6))),
                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                self.task_field,
                ft.Row([
                    ft.Text("Enter a goal. Chimera will inspect, plan and ask before protected changes.", color=MUTED, size=9),
                    ft.Container(expand=True),
                    ft.TextButton("RUN WORKFLOW", on_click=self._run_multi_capability_workflow, disabled=self.task_busy, style=ft.ButtonStyle(color=SECONDARY)),
                    self._button("STOP", self._cancel_conversation) if self.task_busy else self._button("SEND  ↑", self._send_conversation),
                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            ], spacing=9),
            bgcolor="#141A16", border=ft.Border.all(1, BORDER), border_radius=12,
            padding=ft.Padding.all(14), margin=ft.Margin.symmetric(horizontal=22, vertical=14),
        )

        center = ft.Column([
            header,
            ft.Container(
                ft.Column(self._conversation_content(), expand=True, scroll=ft.ScrollMode.AUTO, spacing=16),
                expand=True, padding=ft.Padding.symmetric(horizontal=24, vertical=18),
            ),
            composer,
        ], expand=True, spacing=0)

        self.body.padding = ft.Padding.all(0)
        self.body.content = ft.Row([self._chat_sidebar(), ft.Container(center, expand=True, bgcolor=BG)], expand=True, spacing=0)
        self.page.update()

    def _toggle_agent_mode(self, e=None):
        current = (self.agent_store.session(self.agent_session_id) or {}).get("mode", "plan")
        try:
            self.agent_store.set_mode(self.agent_session_id, "build" if current == "plan" else "plan")
            self._show_overview()
        except Exception as exc:
            self._toast(f"Mode change unavailable: {exc}")
    async def _verify_local_agent(self, e=None):
        if getattr(self, "model_verifying", False):
            return
        self.model_verifying = True
        self._show_overview()
        try:
            selection = self.agent_store.session_model(self.agent_session_id)
            profile_id = selection.get("profile_id")
            if profile_id:
                profile = next((item for item in self.profile_store.list() if item.profile_id == profile_id and item.enabled), None)
                if profile is None:
                    raise RuntimeError("the conversation's selected model profile is missing or disabled")
                if profile.hosted:
                    raise RuntimeError("the selected conversation model is hosted; choose a local GGUF profile before running this local-only check")
            provider = self.agent_runtime._provider_for(self.agent_session_id)
            verifier = getattr(provider, "verify_agent_protocol", None)
            if not callable(verifier):
                raise RuntimeError("the selected local provider does not support Chimera's structured agent protocol test")
            result = await asyncio.to_thread(verifier)
            self.local_verification = result
            self._toast(f"Local agent protocol verified in {result['duration_ms']} ms. No hosted provider was used.")
        except Exception as exc:
            self.local_verification = None
            self._toast(f"Local model verification failed: {exc}")
        finally:
            self.model_verifying = False
            self._show_overview()

    async def _send_conversation(self, e=None):
        if self.task_busy:
            return self._toast("A request is already running.")
        content = (self.task_field.value or "").strip()
        if not content:
            return self._toast("Write a message first.")
        self.task_busy = True
        self.last_turn_notice = None
        self.task_field.value = ""
        self._show_overview()
        self.page.run_task(self._watch_agent)
        try:
            result = await asyncio.to_thread(self.agent_runtime.send, self.agent_session_id, content)
            if result.get("status") == "awaiting_approval":
                self.pending_agent_approval = result
            elif result.get("status") == "failed":
                self.last_turn_notice = f"Local agent request failed: {result.get('error', 'unknown error')}"
            elif result.get("status") == "cancelled":
                self.last_turn_notice = "The request was cancelled. No further tool actions were started."
        except Exception as exc:
            self.last_turn_notice = f"Local agent request failed: {exc}"
            self._toast(self.last_turn_notice)
        finally:
            self.task_busy = False
            self._show_overview()
    async def _run_multi_capability_workflow(self, e=None):
        if self.task_busy:
            return self._toast("A request is already running.")
        if not self.workspace_path:
            return self._toast("Select a workspace before running a multi-step workflow.")
        objective = (self.task_field.value or "").strip()
        if not objective:
            return self._toast("Describe the workflow objective first.")
        self.task_busy = True
        self.conversation_messages.append(("user", f"[MULTI-STEP WORKFLOW] {objective}"))
        self.task_field.value = ""
        self._show_overview()
        try:
            orchestrator = AgenticWorkflowOrchestrator(
                self.agent_runtime, self.agent_runtime.task_store, self.workspace_path
            )
            result = await asyncio.to_thread(
                orchestrator.execute_objective, self.agent_session_id, objective
            )
            if result.get("status") == "awaiting_approval":
                self.pending_agent_approval = self.agent_store.pending_tool(self.agent_session_id)
                self.conversation_messages.append(("assistant", f"Workflow paused for approval after {result.get('steps_executed', 0)} steps. Plan {result.get('plan_id')}"))
            elif result.get("status") == "completed":
                self.conversation_messages.append(("assistant", f"Workflow completed: {result.get('steps_executed', 0)}/{result.get('steps_total', 0)} steps. Plan {result.get('plan_id')}"))
            else:
                self.conversation_messages.append(("assistant", f"Workflow {result.get('status')}: {result.get('steps_executed', 0)}/{result.get('steps_total', 0)} steps. Plan {result.get('plan_id')}"))
        except Exception as exc:
            self.conversation_messages.append(("assistant", f"Workflow could not run: {exc}"))
            self._toast(f"Workflow failed: {exc}")
        finally:
            self.task_busy = False
            self._show_overview()

    async def _pick_workspace(self, e=None):
        try:
            selected = await self.workspace_picker.get_directory_path()
            if selected:
                self._select_workspace(selected)
        except Exception as exc:
            self._toast(f"Directory picker failed: {exc}")
    def _select_workspace(self, selected):
        try:
            path = validate_workspace_path(selected)
            self.workspace_path = path
            self.workspace_path_field.value = str(path)
            self.agent_session_id = self.agent_runtime.create_session(path)
            self.conversation_messages = []
            self.pending_agent_approval = None
            self.local_verification = None
            self.last_turn_notice = None
            self._bind_agent_session()
            self._show_overview()
        except Exception as exc:
            self._toast(f"Workspace selection failed: {exc}")
    def _use_workspace_path(self, e=None):
        self._select_workspace(self.workspace_path_field.value)
    def _bind_agent_session(self):
        try:
            if getattr(self, "agent_unsubscribe", None):
                self.agent_unsubscribe()
        except Exception:
            pass
        self.live_events = []
        self.live_event_count = 0
        self.agent_unsubscribe = self.agent_runtime.subscribe(self.agent_session_id, self._on_agent_event)

    def _on_agent_event(self, event):
        self.live_event_count = event.sequence
        self.live_events.append((event.event_type, event.payload))
        self.live_events = self.live_events[-100:]
    async def _watch_agent(self):
        while self.task_busy:
            await asyncio.sleep(0.25)
            if self.task_busy: self._show_overview()
    async def _cancel_conversation(self, e=None):
        if self.task_busy:
            self.agent_runtime.cancel(self.agent_session_id)
            self._toast("Cancellation requested; the active model/tool boundary will stop safely.")
    async def _approve_agent_tool(self, approved):
        if self.task_busy: return
        self.task_busy = True
        try:
            pending = self.agent_store.pending_tool(self.agent_session_id)
            if pending and str(pending.get("invocation_id", "")).startswith("wf:"):
                orchestrator = AgenticWorkflowOrchestrator(
                    self.agent_runtime, self.agent_runtime.task_store, self.workspace_path
                )
                result = await asyncio.to_thread(
                    orchestrator.approve_pending, self.agent_session_id, approved
                )
                self.pending_agent_approval = self.agent_store.pending_tool(self.agent_session_id)
                if result.get("status") == "completed":
                    self.conversation_messages.append(("assistant", f"Workflow completed: {result.get('steps_executed', 0)}/{result.get('steps_total', 0)} steps. Plan {result.get('plan_id')}"))
                elif result.get("status") == "awaiting_approval":
                    self.conversation_messages.append(("assistant", "The workflow reached another protected action and is awaiting your approval."))
                elif result.get("status") != "denied":
                    self.conversation_messages.append(("assistant", f"Workflow {result.get('status')}: inspect the persisted workflow timeline for details."))
            else:
                result = await asyncio.to_thread(self.agent_runtime.approve_pending, self.agent_session_id, approved)
                self.pending_agent_approval = None
                if result.get("status") == "completed": self.conversation_messages.append(("assistant", result["message"].content))
                elif result.get("status") not in {"denied"}: self.conversation_messages.append(("assistant", f"The approved operation did not complete: {result.get('error', 'unknown error')}"))
        except Exception as exc: self._toast(f"Approval handling failed: {exc}")
        finally:
            self.task_busy = False; self._show_overview()
    def _metric(self, label, value): return ft.Container(ft.Column([ft.Text(label, color=MUTED, size=9), ft.Text(value, color=TEXT, size=13, weight=ft.FontWeight.BOLD)], spacing=5), width=180, bgcolor=SURFACE, border=ft.Border.all(1, BORDER), border_radius=6, padding=ft.Padding.all(14))
    async def _execute_workspace(self, e=None, approve_writes=False):
        if self.task_busy: return self._toast("A request is already running.")
        if not self.workspace_path: return self._toast("Select a workspace directory first.")
        objective = (self.task_field.value or "").strip()
        if not objective: return self._toast("Describe an objective first.")
        self.task_busy = True
        try:
            self._toast("Planning with the local model and executing trusted workspace tools...")
            data_dir = self.registry.data_dir; output = getattr(self, "pending_execution_output", None) or (data_dir / "executions" / "workspace-artifacts" / __import__("uuid").uuid4().hex)
            result = await asyncio.to_thread(execute_objective, objective, self.workspace_path, output, LocalModelProvider(config_from_env()), approve_writes, getattr(self, "pending_execution_plan", None))
            if result.get("status") == "awaiting_approval": self.pending_execution_plan = result.get("plan"); self.pending_execution_output = output
            elif result.get("status") == "completed": self.pending_execution_plan = None
            (output / "execution-trace.json").parent.mkdir(parents=True, exist_ok=True)
            (output / "execution-trace.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
            self._toast(f"Workspace execution {result.get('status')}: {len(result.get('operations', []))} operations")
            self._show_runs()
        except Exception as exc: self._toast(f"Workspace execution failed: {exc}")
        finally: self.task_busy = False
    def _workspace_picked(self, e):
        selected = getattr(e, "path", None)
        if selected:
            self._select_workspace(selected)
    def _new_agent_session(self):
        self.agent_session_id = self.agent_runtime.create_session(self.workspace_path)
        self.conversation_messages = []
        self.pending_agent_approval = None
        self.local_verification = None
        self.last_turn_notice = None
        self._bind_agent_session()
        if getattr(self, "drawer", None):
            self.drawer.visible = False
        self._show_overview()

    def _resume_agent_session(self, session_id):
        session = self.agent_store.session(session_id)
        if not session:
            return self._toast("That session is no longer available.")
        self.agent_session_id = session_id
        self.workspace_path = Path(session["workspace"]) if session.get("workspace") else None
        self.workspace_path_field.value = str(self.workspace_path or "")
        self.conversation_messages = [(m.role, m.content) for m in self.agent_store.messages(session_id) if m.role in {"user", "assistant"}]
        self.pending_agent_approval = self.agent_store.pending_tool(session_id)
        self.local_verification = None
        self.last_turn_notice = None
        self._bind_agent_session()
        if getattr(self, "drawer", None):
            self.drawer.visible = False
        self._show_overview()
    async def _submit_task(self, e=None):
        if self.pending_session_id and self.pending_state != "pending": return self._toast("This continuation requires recovery reconciliation before answers can be submitted.")
        if self.task_busy: return self._toast("A request is already being analyzed.")
        request = self.pending_request or (self.task_field.value or "").strip()
        if not request: return self._toast("Describe a capability request first.")
        clarifications = {"answers": self.clarification_field.value.strip()} if self.pending_request else None
        self.task_busy = True
        try:
            self._toast("Analyzing with the configured local model...")
            workflow = CapabilityWorkflow(LocalModelProvider(config_from_env()), self.registry.data_dir / "candidates", registry=self.registry)
            result = await asyncio.to_thread(workflow.resume_clarification, self.pending_session_id, {q.question_id: self.clarification_field.value.strip()} if self.pending_session_id else {}) if self.pending_session_id else await asyncio.to_thread(workflow.run, request, clarifications)
            if result.state == "needs_clarification": self.pending_request, self.pending_questions, self.pending_session_id = request, result.readiness.questions, result.clarification_session_id
            else: self.pending_request, self.pending_questions, self.pending_session_id = None, (), None
            self._toast(result.message); self._show_overview() if result.state == "needs_clarification" else self._show_capabilities()
        except Exception as exc:
            self._toast(f"Workflow failed: {exc}")
        finally:
            self.task_busy = False
    def _inspect(self, c):
        self.selected = c
        try: report, history = persisted_evaluation(c, AuditStore(self.registry.data_dir / "chimera.db"))
        except Exception as exc: report, history = {"status": "unavailable", "reason": str(exc)}, []
        digest = c.manifest_digest or c.digest or "UNVERIFIED"
        details = f"{c.name}\n\nSTATE  {_upper(c.status)}\nVERSION  {c.version}\nTASK  {c.task}\nMANIFEST  {digest}\nACCEPTANCE APPROVAL  {'GRANTED' if c.acceptance_approved else 'REQUIRED'}\nEXECUTION APPROVAL  {'GRANTED' if c.execution_approved else 'REQUIRED'}\n\nCONTRACT / STATIC evidence is separate from BEHAVIORAL evidence. No behavioral success is shown without a persisted authoritative record."
        criteria = [ft.Container(ft.Column([ft.Row([ft.Text(label, color=TEXT, weight=ft.FontWeight.BOLD), ft.Text(_upper(state), color=STATE_COLORS.get(state, MUTED), size=10)], alignment=ft.MainAxisAlignment.SPACE_BETWEEN), ft.Text(reason, color=SECONDARY, size=11), ft.Text(refs, color=MUTED, size=10)]), bgcolor=SURFACE, border=ft.Border.all(1, STATE_COLORS.get(state, BORDER)), border_radius=6, padding=ft.Padding.all(12), width=760) for label, state, reason, refs in evaluation_rows(report)]
        coverage = [ft.Container(ft.Column([ft.Row([ft.Text(label, color=TEXT, weight=ft.FontWeight.BOLD), ft.Text(_upper(state), color=FOCUS if state == "full" else MUTED, size=10)], alignment=ft.MainAxisAlignment.SPACE_BETWEEN), ft.Text(reason, color=SECONDARY, size=11)]), bgcolor=SURFACE, border=ft.Border.all(1, BORDER), border_radius=6, padding=ft.Padding.all(12), width=760) for label, state, reason in traceability_rows(getattr(c, "requirement_trace", None))]
        history_controls = [ft.Text(f"{event['stage']}  /  {event['outcome']}  /  {event['created_at'] or 'timestamp unavailable'}", color=MUTED, size=10) for event in history]
        self._set([ft.Text(f"CANDIDATE / {c.candidate_id}", size=22, color=TEXT, weight=ft.FontWeight.BOLD), ft.Container(ft.Text(details, color=SECONDARY, size=12), bgcolor=SURFACE, border=ft.Border.all(1, BORDER), border_radius=8, padding=ft.Padding.all(20), width=760), ft.Text("REQUIREMENT TRACEABILITY", color=MUTED, size=10), *coverage, ft.Text("EVALUATION CRITERIA", color=MUTED, size=10), *criteria, ft.Text("PERSISTED WORKFLOW EVIDENCE", color=MUTED, size=10), *(history_controls or [ft.Text("No relevant workflow history is available.", color=MUTED, size=11)]), ft.Row([ft.TextButton("VERIFY STATIC", on_click=lambda e: self._action(lambda: self.review.verify(c.candidate_id)), style=ft.ButtonStyle(color=TEXT)), ft.TextButton("BEHAVIOR CHECK", on_click=lambda e: self._action(lambda: self.review.behavior_verify(c.candidate_id)), style=ft.ButtonStyle(color=FOCUS)), ft.TextButton("APPROVE ACCEPTANCE", on_click=lambda e: self._action(lambda: self.review.approve_acceptance(c.candidate_id)), style=ft.ButtonStyle(color=TEXT)), ft.TextButton("ACCEPT", on_click=lambda e: self._action(lambda: self.review.accept(c.candidate_id), True), style=ft.ButtonStyle(color=TEXT))])])
