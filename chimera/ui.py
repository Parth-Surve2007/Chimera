"""Monochrome Flet command console for Chimera."""
import sys, asyncio, json
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
try: import flet as ft
except ImportError: ft = None

BG, SURFACE, ELEVATED, BORDER = "#070808", "#101313", "#171B1A", "#29302E"
TEXT, SECONDARY, MUTED, DISABLED, FOCUS = "#F3F5F3", "#A7B0AC", "#68736E", "#3F4945", "#8AAE9C"
ACCENT = FOCUS

STATE_COLORS = {"pass": "#8AAE9C", "fail": "#D9897A", "unknown": "#C7A66A", "blocked": "#B77D9A"}

def _upper(value, default="UNKNOWN"):
    """Format optional persisted/status values without allowing the UI to crash."""
    return str(value or default).upper()

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
        self.task_busy = False; self.pending_request = None; self.pending_session_id = None; self.pending_questions = (); self.pending_state = None; self.pending_reason = ""; self.clarification_field = ft.TextField(multiline=True, min_lines=3, max_lines=6, hint_text="Answer the clarification questions...", hint_style=ft.TextStyle(color=MUTED), bgcolor=BG, color=TEXT, border_color=BORDER, focused_border_color=FOCUS, border_radius=6, expand=True); self.task_field = ft.TextField(multiline=True, min_lines=4, max_lines=7, hint_text="Ask Chimera anything. Describe what you want accomplished...", hint_style=ft.TextStyle(color=MUTED), bgcolor=BG, color=TEXT, border_color=BORDER, focused_border_color=FOCUS, border_radius=6, expand=True)
        self.agent_store = AgentSessionStore(registry.data_dir / "chimera.db")
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
        return ft.Container(ft.Row([ft.Row([ft.Text("◢", color=TEXT, size=19), ft.Text("CHIMERA", color=TEXT, size=14, weight=ft.FontWeight.BOLD)], spacing=9), ft.Row([ft.Text(self._runtime_state(), color=MUTED, size=10), ft.IconButton(icon=ft.Icons.MENU, icon_color=SECONDARY, tooltip="Navigation", on_click=self._toggle_drawer)], spacing=16)], alignment=ft.MainAxisAlignment.SPACE_BETWEEN), height=50, padding=ft.Padding.symmetric(horizontal=24), border=ft.Border.only(bottom=ft.BorderSide(1, BORDER)))
    def _drawer(self):
        buttons = []
        buttons.append(ft.TextButton("NEW CONVERSATION", on_click=lambda e: self._new_agent_session(), style=ft.ButtonStyle(color=FOCUS, padding=ft.Padding.symmetric(horizontal=18, vertical=14))))
        for item in self.agent_store.list_sessions()[:8]:
            buttons.append(ft.TextButton(f"↳ {item['title'][:24]}", on_click=lambda e, sid=item["session_id"]: self._resume_agent_session(sid), style=ft.ButtonStyle(color=SECONDARY, padding=ft.Padding.symmetric(horizontal=18, vertical=8))))
        for label, handler in [("OVERVIEW", self._show_overview), ("MODEL HUB", self._show_model_hub), ("WORKFLOW STUDIO", self._show_workflow_studio), ("CAPABILITIES", self._show_capabilities), ("RUNS", self._show_runs), ("SETTINGS", self._show_settings)]:
            buttons.append(ft.TextButton(label, on_click=lambda e, h=handler: self._navigate(h), style=ft.ButtonStyle(color=TEXT, padding=ft.Padding.symmetric(horizontal=18, vertical=14))))
        return ft.Container(ft.Column([ft.Text("NAVIGATION", color=MUTED, size=10), *buttons], spacing=3), width=210, right=18, top=10, bgcolor=SURFACE, border=ft.Border.all(1, BORDER), padding=ft.Padding.all(14), visible=False, shadow=ft.BoxShadow(blur_radius=18, color="#000000"))
    def _toggle_drawer(self, e=None): self.drawer.visible = not self.drawer.visible; self.page.update()
    def _navigate(self, handler): self.drawer.visible = False; handler(); self.page.update()
    def _runtime_state(self): return "RUNTIME: READY" if check_status(config_from_env()).state == "ready" else "RUNTIME: UNCONFIGURED"
    def _statusbar(self):
        try: caps = len(self.registry.list()); review = len(self.review.list())
        except Exception: caps, review = "?", "?"
        return ft.Container(ft.Text(f"MODEL: {self._runtime_state().split(': ')[-1]}   ·   CAPABILITIES: {caps}   ·   REVIEW: {review}   ·   STATE: IDLE", color=MUTED, size=10), height=34, padding=ft.Padding.symmetric(horizontal=24, vertical=8), border=ft.Border.only(top=ft.BorderSide(1, BORDER)))
    def _set(self, controls): self.body.content = ft.Column(controls, expand=True, scroll=ft.ScrollMode.AUTO, horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=18); self.page.update()
    def _button(self, label, handler): return ft.FilledButton(label, on_click=handler, style=ft.ButtonStyle(bgcolor=TEXT, color=BG, overlay_color=SECONDARY))
    def _show_overview(self, e=None):
        composer = ft.Container(ft.Column([ft.Text("NEW TASK", color=MUTED, size=10), ft.TextField(multiline=True, min_lines=3, max_lines=5, hint_text="What should Chimera build?", hint_style=ft.TextStyle(color=MUTED), bgcolor=BG, color=TEXT, border_color=BORDER, focused_border_color=FOCUS, border_radius=3), ft.Row([ft.Text("LOCAL RUNTIME UNCONFIGURED" if "UNCONFIGURED" in self._runtime_state() else "LOCAL RUNTIME READY", color=MUTED, size=10), self._button("SUBMIT  ↑", lambda e: self._toast("Configure the local runtime before submitting a task."))], alignment=ft.MainAxisAlignment.SPACE_BETWEEN)], spacing=12), width=720, bgcolor=SURFACE, border=ft.Border.all(1, BORDER), padding=ft.Padding.all(22))
        self._set([ft.Container(expand=True), ft.Text("◢", font_family="ChimeraDots", size=46, color=SECONDARY), ft.Text("CHIMERA", font_family="ChimeraDots", size=34, color=TEXT), ft.Text("LOCAL-FIRST CAPABILITY ENGINE", size=10, color=MUTED), ft.Text("Build capabilities · verify results · preserve what works", size=12, color=SECONDARY), composer, ft.Row([ft.TextButton("CAPABILITIES", on_click=self._show_capabilities, style=ft.ButtonStyle(color=MUTED)), ft.Text("·", color=DISABLED), ft.TextButton("REVIEW QUEUE", on_click=self._show_capabilities, style=ft.ButtonStyle(color=MUTED)), ft.Text("·", color=DISABLED), ft.TextButton("RECENT RUNS", on_click=self._show_runs, style=ft.ButtonStyle(color=MUTED))], alignment=ft.MainAxisAlignment.CENTER), ft.Container(expand=True)])
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
        cards = [ft.Container(ft.Column([ft.Text(f"{p.display_name or p.model_id}  ·  {p.provider_id}  ·  {'API' if p.hosted else 'LOCAL'}", color=TEXT), ft.Text(f"CAPABILITIES: {', '.join(p.modalities)}  TOOLS: {p.tool_calling}  STRUCTURED: {p.structured_output}  STATE: {'ENABLED' if p.enabled else 'DISABLED'}  EVIDENCE: {p.capability_evidence}", color=SECONDARY, size=11), ft.Row([ft.TextButton("USE", on_click=lambda e, pid=p.profile_id: self._select_session_model(pid), style=ft.ButtonStyle(color=FOCUS)), ft.TextButton("DEFAULT", on_click=lambda e, pid=p.profile_id: self._set_default_model(pid), style=ft.ButtonStyle(color=FOCUS)), ft.TextButton("TOGGLE", on_click=lambda e, pid=p.profile_id: self._toggle_model(pid), style=ft.ButtonStyle(color=SECONDARY)), ft.TextButton("REMOVE", on_click=lambda e, pid=p.profile_id: self._remove_model(pid), style=ft.ButtonStyle(color=SECONDARY))])]), padding=ft.Padding.all(12), border=ft.Border.all(1, BORDER)) for p in profiles]
        form = ft.Container(ft.Column([ft.Text("ADD OR EDIT PROFILE", color=MUTED, size=10), self.hub_id, self.hub_provider, self.hub_model, self.hub_url, self.hub_secret, ft.Row([self._button("SAVE LOCAL", lambda e: self._save_model_profile(False)), self._button("SAVE API", lambda e: self._save_model_profile(True)), ft.TextButton("TEST SELECTED API", on_click=self._test_model_profile, style=ft.ButtonStyle(color=FOCUS))])], spacing=8), bgcolor=SURFACE, padding=ft.Padding.all(14), border=ft.Border.all(1, BORDER))
        self._set([ft.Text("MODEL INTELLIGENCE HUB", size=18, color=TEXT), ft.Text("Hosted providers are never contacted during profile browsing or saving. Credentials are stored separately in Windows Credential Manager.", color=MUTED, size=11), form, *(cards or [ft.Text("No model profiles configured yet.", color=SECONDARY)])])
    def _select_session_model(self, profile_id):
        try: self.agent_runtime.set_session_model(self.agent_session_id, profile_id, local_only=True); self._toast("Model pinned for future turns in this session."); self._show_model_hub()
        except Exception as exc: self._toast(f"Model selection failed: {exc}")
    def _save_model_profile(self, hosted):
        try:
            from .model_hub import ModelProfile
            profile = ModelProfile(self.hub_id.value.strip(), self.hub_provider.value.strip(), self.hub_model.value.strip(), hosted, model_path=None if hosted else self.hub_model.value.strip(), base_url=self.hub_url.value.strip() or None, tool_calling=False, structured_output=False, capability_evidence="configured")
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
            result = await asyncio.to_thread(OpenAICompatibleAdapter(profile).generate, [{"role": "user", "content": "Reply with the word test."}], 20)
            self._toast(f"Provider ready: {result.get('provider')} / {result.get('model')} ({result.get('duration_ms')} ms)")
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
    def _show_runs(self, e=None): self._set([ft.Text("RUNS", size=18, color=TEXT), ft.Text("No executions recorded.", color=MUTED)])
    def _show_settings(self, e=None):
        c = config_from_env(); s = check_status(c); source = config_source(); policy = load_policy(self.registry.data_dir); server = self.local_provider.server.status() if self.local_provider.server else {"state": "unavailable"}
        self._set([ft.Text("SETTINGS", size=18, color=TEXT), ft.Text(f"LOCAL RUNTIME: {_upper(s.state)}\nSERVER: {_upper(server['state'])}\nMODEL: {c.model_path or 'NOT CONFIGURED'} ({source['model']})\nSERVER EXECUTABLE: {c.server_path or 'NOT AVAILABLE'}\nSERVER PID: {server.get('pid') or 'none'}  PORT: {server.get('url') or 'none'}\nCONTEXT: {c.context}  TOKENS: {c.max_tokens}  THREADS: {c.threads}  GPU LAYERS: {c.gpu_layers}\nPOLICY: {policy['mode']}  IDLE TIMEOUT: {policy['idle_timeout']}s\n\nDATA: {self.registry.data_dir}\nEnvironment variables override persisted model_config.json.", color=SECONDARY), ft.Row([self.model_path_field, ft.TextButton("SELECT GGUF", on_click=lambda e: self.model_picker.pick_files(allowed_extensions=["gguf"]))]), ft.Row([self.runtime_path_field, ft.TextButton("SELECT CLI RUNTIME", on_click=lambda e: self.runtime_picker.pick_files())]), ft.Row([self._button("SAVE + VALIDATE", self._save_model_config), ft.TextButton("START SERVER", on_click=self._start_local_server, style=ft.ButtonStyle(color=TEXT)), ft.TextButton("STOP SERVER", on_click=self._stop_local_server, style=ft.ButtonStyle(color=TEXT))]), ft.Text("Managed server is loopback-only and starts lazily. Models are never downloaded or bundled.", color=MUTED, size=11)])
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
        save_persisted_config(model, runtime); self._toast(f"Saved. Provider state: {_upper(check_status(config_from_env()).state)}"); self._show_settings()
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
    # Refined operational overview; this definition intentionally replaces the legacy landing layout.
    def _show_overview(self, e=None):
        # Conversation remains available even when the configured provider is unavailable;
        # send() will persist and report the real provider error.
        ready = True
        request = getattr(self, "task_field", None)
        if request is None:
            self.task_field = ft.TextField(multiline=True, min_lines=4, max_lines=7, hint_text="What would you like Chimera to accomplish?", hint_style=ft.TextStyle(color=MUTED), bgcolor=BG, color=TEXT, border_color=BORDER, focused_border_color=FOCUS, border_radius=6, expand=True)
        activity = []
        try:
            events = self.agent_store.events(self.agent_session_id)
            for event in events:
                if event.event_type == "tool.call.requested": activity.append(ft.Text(f"TOOL REQUESTED  {event.payload.get('tool')}  {json.dumps(event.payload.get('arguments', {}))}", color=FOCUS, size=10))
                elif event.event_type in {"tool.completed", "tool.failed"}: activity.append(ft.Text(f"TOOL {event.event_type.split('.')[-1].upper()}  {event.payload.get('tool', '')}  {event.payload.get('status', '')}", color=SECONDARY, size=10))
        except Exception: pass
        approval = []
        if self.pending_agent_approval:
            approval = [ft.Container(ft.Column([ft.Text("APPROVAL REQUIRED", color=FOCUS, size=10), ft.Text(f"{self.pending_agent_approval.get('tool')}\n{json.dumps(self.pending_agent_approval.get('arguments', {}), indent=2)}", color=TEXT, size=11), ft.Row([self._button("APPROVE THIS OPERATION", lambda e: self._approve_agent_tool(True)), ft.TextButton("REJECT", on_click=lambda e: self._approve_agent_tool(False), style=ft.ButtonStyle(color=SECONDARY))])], spacing=8), bgcolor=ELEVATED, border=ft.Border.all(1, FOCUS), border_radius=6, padding=ft.Padding.all(14))]
        composer = ft.Container(ft.Column([ft.Text("CONVERSATION", color=MUTED, size=10), *[ft.Text(f"{role.upper()}: {text}", color=TEXT if role == "user" else SECONDARY, size=12) for role, text in self.conversation_messages[-8:]], *activity, *approval, self.task_field, ft.Row([ft.Text("Local reasoning · persistent session", color=MUTED, size=10), self._button("STOP", self._cancel_conversation) if self.task_busy else self._button("SEND", self._send_conversation) if ready else ft.TextButton("CONFIGURE RUNTIME", on_click=self._show_settings, style=ft.ButtonStyle(color=FOCUS))], alignment=ft.MainAxisAlignment.SPACE_BETWEEN)], spacing=14), width=760, bgcolor=SURFACE, border=ft.Border.all(1, BORDER), border_radius=8, padding=ft.Padding.all(24))
        clarification = [ft.Row([ft.Text(f"PROJECT: {self.workspace_path or 'NOT SELECTED'}", color=MUTED, size=10), ft.TextButton("SELECT DIRECTORY", on_click=lambda e: self.workspace_picker.get_directory_path(), style=ft.ButtonStyle(color=FOCUS))], alignment=ft.MainAxisAlignment.SPACE_BETWEEN)]
        if self.pending_questions:
            if self.pending_state == "pending":
                content = [ft.Text("CLARIFICATION REQUIRED", color=FOCUS, size=10), *[ft.Text(f"{q.question_id}: {q.question}\nWhy: {q.reason}", color=SECONDARY, size=11) for q in self.pending_questions], self.clarification_field, self._button("SUBMIT CLARIFICATIONS", self._submit_task)]
            else:
                content = [ft.Text(f"CONTINUATION {_upper(self.pending_state)}", color=FOCUS, size=10), ft.Text(self.pending_reason, color=SECONDARY, size=11), ft.Text("No answer will be submitted and no retry is performed automatically.", color=MUTED, size=11)]
            clarification = [ft.Container(ft.Column(content, spacing=10), width=760, bgcolor=SURFACE, border=ft.Border.all(1, FOCUS), border_radius=8, padding=ft.Padding.all(18))]
        self._set([ft.Container(ft.Text("CHIMERA", font_family="ChimeraDots", size=72, color="#18201D", opacity=0.5), height=90), ft.Text("What would you like Chimera to accomplish?", size=30, weight=ft.FontWeight.BOLD, color=TEXT), ft.Text("Local reasoning, explicit verification, human-controlled acceptance.", size=13, color=SECONDARY), composer, *clarification, ft.Row([self._metric("MODEL", self._runtime_state().replace("RUNTIME: ", "")), self._metric("REVIEW QUEUE", str(len(self.review.list()))), self._metric("BEHAVIOR", "BLOCKED" if not VirtualBoxBackend().inspect().get("available") else "AVAILABLE")], spacing=12), ft.Text("No capability is accepted or executed automatically.", color=MUTED, size=11)])
    async def _send_conversation(self, e=None):
        if self.task_busy: return self._toast("A request is already running.")
        content = (self.task_field.value or "").strip()
        if not content: return self._toast("Ask Chimera a question or describe an objective first.")
        self.task_busy = True; self.conversation_messages.append(("user", content)); self.task_field.value = ""; self._show_overview()
        try:
            self.page.run_task(self._watch_agent)
            result = await asyncio.to_thread(self.agent_runtime.send, self.agent_session_id, content)
            if result.get("status") == "completed": self.conversation_messages.append(("assistant", result["message"].content))
            elif result.get("status") == "awaiting_approval": self.pending_agent_approval = result
            else: self.conversation_messages.append(("assistant", f"I could not complete that locally: {result.get('error', 'unknown error')}"))
        finally:
            self.task_busy = False; self._show_overview()
    def _on_agent_event(self, event):
        self.live_event_count = event.sequence
        self.live_events.append((event.event_type, event.payload))
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
        if selected and Path(selected).is_dir():
            self.workspace_path = Path(selected).resolve()
            self.agent_session_id = self.agent_runtime.create_session(self.workspace_path)
            self.conversation_messages = []
            self._show_overview()
    def _new_agent_session(self):
        self.agent_session_id = self.agent_runtime.create_session(self.workspace_path)
        self.conversation_messages = []; self.pending_agent_approval = None; self.drawer.visible = False; self._show_overview()
    def _resume_agent_session(self, session_id):
        session = self.agent_store.session(session_id)
        if not session: return self._toast("That session is no longer available.")
        self.agent_session_id = session_id
        self.workspace_path = Path(session["workspace"]) if session.get("workspace") else None
        self.conversation_messages = [(m.role, m.content) for m in self.agent_store.messages(session_id) if m.role in {"user", "assistant"}]
        self.pending_agent_approval = self.agent_store.pending_tool(session_id)
        self.drawer.visible = False; self._show_overview()
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
