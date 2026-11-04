"""Dark Emperor Flet command console for Chimera."""
import sys, asyncio, json, os
from pathlib import Path
from .review import ReviewService
from .workflow import CapabilityWorkflow
from .behavior import VirtualBoxBackend
from .server_provider import load_policy, save_policy
from .model_hub import ModelProfileStore, CredentialStore, OpenAICompatibleAdapter, ModelProfile
from .audit import AuditStore
from .local_model import config_from_env, check_status, save_persisted_config, LocalModelProvider, load_persisted_config, config_data_dir, config_source
from .agent_loop import execute_objective
from .tools import CAPABILITY
from .agent_runtime import AgentRuntime, AgentSessionStore
from .goals import GoalStore
from .workflow_integration import AgenticWorkflowOrchestrator
from .builtin_capabilities import BUILTIN_CAPABILITIES
try: import flet as ft
except ImportError: ft = None

# Xerxes-inspired Dark Emperor system: true-black canvas, graphite surfaces,
# warm metal focus states and restrained ember feedback.
BG, SURFACE, ELEVATED, BORDER = "#000000", "#0B0B0B", "#141414", "#2B2B2B"
TEXT, SECONDARY, MUTED, DISABLED, FOCUS = "#F3F3F3", "#C5C5C5", "#858585", "#4C4C4C", "#E5B35A"
ACCENT = "#F17857"
PANEL_WARM = "#12100F"
STATE_COLORS = {"pass": "#E5B35A", "fail": "#F17857", "unknown": "#BFA16A", "blocked": "#8B8B8B"}

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


def default_workspace_root(system_drive=None, platform=None, home=None):
    """Use the Windows system drive by default, or the home directory elsewhere."""
    platform = platform or os.name
    if platform == "nt":
        drive = (system_drive or os.environ.get("SystemDrive") or "C:").rstrip("\\/")
        return Path(drive + "\\")
    return Path(home or Path.home()).expanduser().resolve()

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
        initial_config = config_from_env()
        self.model_picker = ft.FilePicker(on_result=self._model_picked)
        self.runtime_picker = ft.FilePicker(on_result=self._runtime_picked)
        self.server_picker = ft.FilePicker(on_result=self._server_picked)
        self.workspace_path = validate_workspace_path(default_workspace_root())
        page.services.extend([self.model_picker, self.runtime_picker, self.server_picker])
        self.model_path_field = ft.TextField(label="GGUF MODEL PATH", color=TEXT, border_color=BORDER, value=str(initial_config.model_path or ""), expand=True)
        self.runtime_path_field = ft.TextField(label="LLAMA.CPP CLI (EXPLICIT ONE-SHOT MODE)", color=TEXT, border_color=BORDER, value=str(initial_config.cli_path or ""), expand=True)
        self.server_path_field = ft.TextField(label="LLAMA-SERVER (PERSISTENT MODEL MODE)", color=TEXT, border_color=BORDER, value=str(initial_config.server_path or ""), expand=True)
        self.context_field = ft.TextField(label="CONTEXT", color=TEXT, border_color=BORDER, value=str(initial_config.context), width=120)
        self.max_tokens_field = ft.TextField(label="MAX TOKENS", color=TEXT, border_color=BORDER, value=str(initial_config.max_tokens), width=120)
        self.threads_field = ft.TextField(label="CPU THREADS", color=TEXT, border_color=BORDER, value=str(initial_config.threads), width=120)
        self.gpu_layers_field = ft.TextField(label="GPU LAYERS", color=TEXT, border_color=BORDER, value=str(initial_config.gpu_layers), width=120)
        page.title = "Chimera"; page.theme_mode = ft.ThemeMode.DARK; page.theme = ft.Theme(font_family="Segoe UI"); page.bgcolor = BG; page.padding = 0; page.window.width = 1440; page.window.height = 900; page.window.min_width = 1120; page.window.min_height = 700
        font = Path(__file__).resolve().parent.parent / "fonts" / "ec-dots-life-font" / "EcDotsLifeRegular-AydL.ttf"
        page.fonts = {"ChimeraDots": str(font)} if font.exists() else {}
        self.logo_font_family = "ChimeraDots" if font.exists() else "Segoe UI"
        self.task_busy = False; self.model_verifying = False; self.local_verification = None; self.model_boot_status = "checking"; self.last_turn_notice = None; self.pending_request = None; self.pending_session_id = None; self.pending_questions = (); self.pending_state = None; self.pending_reason = ""
        self.clarification_field = ft.TextField(multiline=True, min_lines=3, max_lines=6, hint_text="Answer the clarification questions...", hint_style=ft.TextStyle(color=MUTED), bgcolor=BG, color=TEXT, border_color=BORDER, focused_border_color=FOCUS, border_radius=8, expand=True)
        self.task_field = ft.TextField(multiline=True, min_lines=3, max_lines=8, hint_text="Give Chimera the outcome. Keep talking as it works...", hint_style=ft.TextStyle(color=MUTED), bgcolor="#101010", color=TEXT, border_color=BORDER, focused_border_color=FOCUS, border_radius=11, expand=True, text_size=14)
        self.agent_store = AgentSessionStore(registry.data_dir / "chimera.db")
        self.goal_store = GoalStore(registry.data_dir / "chimera.db")
        self.profile_store = ModelProfileStore(registry.data_dir / "model_profiles.json")
        self.local_provider = LocalModelProvider(config_from_env())
        self.agent_runtime = AgentRuntime(self.agent_store, self.local_provider, self.profile_store)
        self.active_page = "chat"
        self.agent_session_id = self.agent_runtime.create_session(self.workspace_path)
        self.conversation_messages = []
        self.pending_agent_approval = None; self.live_events = []
        self.live_event_count = 0
        self.live_status_text = None; self.chat_feed = None; self.current_view = "chat"
        self.send_button = None; self.stop_button = None
        self.runtime_status_text = None; self.chat_model_state_text = None
        self.agent_unsubscribe = self.agent_runtime.subscribe(self.agent_session_id, self._on_agent_event)
        try:
            pending = AuditStore(registry.data_dir / "chimera.db").clarification_pending()
            if pending:
                self.pending_session_id = pending[0]["session_id"]; self.pending_request = pending[0]["original_request"]; self.pending_questions = tuple(type("Question", (), question) for question in pending[0]["questions"]); self.pending_state = pending[0]["state"]
                current_reason = pending[0].get("recovery_reason")
                reason_text = current_reason if current_reason else "No current recovery reason is persisted (legacy or unavailable)."
                self.pending_reason = f"State: {self.pending_state}. Continuation: {pending[0]['current_run_id']}. Recovery reason: {reason_text}. Reconciliation is read-only; no retry is performed automatically." if self.pending_state != "pending" else ""
        except Exception: pass
        self.body = ft.Container(expand=True, padding=ft.Padding.all(0)); self.drawer = self._drawer(); self._show_overview()
        self.shell = ft.Column([self._topbar(), ft.Stack([self.body, self.drawer], expand=True), self._statusbar()], expand=True, spacing=0)
        if not self._show_intro(): page.add(self.shell)
        # Keep a configured local model resident before the first prompt.
        page.run_task(self._warm_local_model)
    def _show_intro(self):
        if str(__import__("os").environ.get("CHIMERA_INTRO", "0")).lower() in ("0", "false", "off"): return False
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
        state_color = FOCUS if local_state in {"READY", "CONFIGURED"} else "#BFA16A"
        logo = ft.Container(
            ft.Row([
                ft.Text("◈", color=ACCENT, size=20),
                ft.Text("CHIMERA", color=TEXT, size=18, weight=ft.FontWeight.BOLD, font_family=getattr(self, "logo_font_family", "Segoe UI")),
                ft.Text("LOCAL AGENT WORKSPACE", color=MUTED, size=9),
            ], spacing=10),
            tooltip="Return to the Chimera chat",
            padding=ft.Padding.symmetric(vertical=8, horizontal=4),
            on_click=lambda e: self._navigate(self._show_overview),
        )
        self.runtime_status_text = ft.Text(f"LOCAL RUNTIME  /  {local_state}", color=SECONDARY, size=10)
        status = ft.Container(
            ft.Row([
                ft.Container(width=6, height=6, bgcolor=state_color, border_radius=6),
                self.runtime_status_text,
            ], spacing=8),
            bgcolor=SURFACE, border=ft.Border.all(1, BORDER), border_radius=20,
            padding=ft.Padding.symmetric(horizontal=13, vertical=8),
        )
        return ft.Container(
            ft.Row([
                logo,
                ft.Row([
                    status,
                    ft.IconButton(
                        icon=ft.Icons.SETTINGS_OUTLINED, icon_color=SECONDARY,
                        tooltip="Model and application settings", on_click=self._show_settings,
                    ),
                ], spacing=10),
            ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            height=62, padding=ft.Padding.symmetric(horizontal=24),
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
        try:
            custom_caps = len(self.registry.list())
            review = len(self.review.list())
        except Exception:
            custom_caps, review = "?", "?"
        return ft.Container(ft.Text(
            f"MODEL: {self._runtime_state().split(': ')[-1]}   ·   BUILT-INS: {len(BUILTIN_CAPABILITIES)}   ·   CUSTOM: {custom_caps}   ·   REVIEW: {review}",
            color=MUTED, size=10,
        ), height=34, padding=ft.Padding.symmetric(horizontal=24, vertical=8), border=ft.Border.only(top=ft.BorderSide(1, BORDER)))

    def _set(self, controls):
        self.current_view = "other"
        content = ft.Column(
            controls, expand=True, scroll=ft.ScrollMode.AUTO,
            horizontal_alignment=ft.CrossAxisAlignment.START, spacing=16,
        )
        centered = ft.Row(
            [ft.Container(
                content=content, width=1000,
                padding=ft.Padding.symmetric(horizontal=24, vertical=22),
            )],
            alignment=ft.MainAxisAlignment.CENTER,
            vertical_alignment=ft.CrossAxisAlignment.START,
            expand=True,
        )
        self.body.padding = ft.Padding.all(0)
        self.body.content = ft.Row([
            self._chat_sidebar(),
            ft.Container(content=centered, expand=True, bgcolor=BG),
        ], expand=True, spacing=0)
        self.page.update()

    def _page_heading(self, title, subtitle, eyebrow="CHIMERA  /  WORKSPACE"):
        return ft.Container(
            ft.Column([
                ft.Text(eyebrow, color=FOCUS, size=9, weight=ft.FontWeight.BOLD),
                ft.Text(title, color=TEXT, size=27, weight=ft.FontWeight.BOLD),
                ft.Text(subtitle, color=SECONDARY, size=12),
            ], spacing=6),
            padding=ft.Padding.only(bottom=8), width=940,
        )

    def _panel(self, title, subtitle, controls, width=940):
        return ft.Container(
            ft.Column([
                ft.Text(title, color=TEXT, size=16, weight=ft.FontWeight.BOLD),
                *([ft.Text(subtitle, color=MUTED, size=11)] if subtitle else []),
                *controls,
            ], spacing=11),
            bgcolor=SURFACE, border=ft.Border.all(1, BORDER),
            border_radius=12, padding=ft.Padding.all(18), width=width,
        )

    def _button(self, label, handler): return ft.FilledButton(label, on_click=handler, style=ft.ButtonStyle(bgcolor=FOCUS, color=BG, overlay_color=ACCENT))


    def _builtin_capability_cards(self):
        try:
            from .applications.registry import get_application_registry
            manifests = {
                manifest.application_id: manifest
                for manifest in get_application_registry().discover_all()
            }
        except Exception:
            manifests = {}

        cards = []
        for capability in BUILTIN_CAPABILITIES:
            capability_id = capability["capability_id"]
            status = "BUILT IN"
            note = capability["description"]
            if capability_id == "computer.windows":
                status = "READY" if os.name == "nt" else "WINDOWS ONLY"
                note += " UI Automation support is installed by the Windows dependency setup."
            elif capability_id.endswith(".blender"):
                manifest = manifests.get("blender")
                status = "INSTALLED" if manifest and manifest.is_installed else "OPTIONAL APP MISSING"
                note += " Blender is a separate application; install it from Applications."
            elif capability_id.endswith(".ghidra"):
                manifest = manifests.get("ghidra")
                if manifest and manifest.is_installed:
                    status = "INSTALLED"
                    if not manifest.metadata.get("jdk_available"):
                        status = "INSTALLED · JDK 25 NEEDED FOR HEADLESS"
                else:
                    status = "OPTIONAL APP MISSING"
                note += " Built-in static inspection remains available; Ghidra headless features need Ghidra and JDK 25+."
            actions = capability.get("actions", ())
            cards.append(ft.Container(
                ft.Column([
                    ft.Row([
                        ft.Text(capability["name"], color=TEXT, size=14, weight=ft.FontWeight.BOLD, expand=True),
                        ft.Text(status, color=FOCUS if status in {"BUILT IN", "READY", "INSTALLED"} else SECONDARY, size=8, weight=ft.FontWeight.BOLD),
                    ]),
                    ft.Text(capability["domain"].upper(), color=ACCENT, size=8, weight=ft.FontWeight.BOLD),
                    ft.Text(note, color=SECONDARY, size=10),
                    ft.Text(f"{len(actions)} registered action(s)" if actions else "Native service / workflow", color=MUTED, size=9),
                ], spacing=6),
                bgcolor=SURFACE, border=ft.Border.all(1, BORDER), border_radius=9,
                padding=ft.Padding.all(13), width=940,
            ))
        return cards

    def _connector_status_cards(self):
        from .applications.registry import get_application_registry
        manifests = getattr(self, "_application_manifests", None)
        if manifests is None:
            manifests = get_application_registry().discover_all()
        cards = []
        for manifest in manifests:
            actions_summary = ", ".join(
                f"{action.display_name} · L{action.risk_level.value}"
                for action in manifest.actions if not getattr(action, "unavailable", False)
            ) or "Connector registered; no executable actions currently available"
            if manifest.application_id == "ghidra" and manifest.is_installed and not manifest.metadata.get("jdk_available"):
                status = "APP FOUND · JDK 25 NEEDED FOR HEADLESS"
            else:
                status = "INSTALLED" if manifest.is_installed else "CONNECTOR READY · APP NOT INSTALLED"
            cards.append(ft.Container(
                ft.Column([
                    ft.Row([
                        ft.Text(manifest.display_name, color=TEXT, size=13, weight=ft.FontWeight.BOLD, expand=True),
                        ft.Text(status, color=FOCUS if manifest.is_installed else SECONDARY, size=8, weight=ft.FontWeight.BOLD),
                    ]),
                    ft.Text(f"{manifest.vendor} · Connector {manifest.connector_version}", color=MUTED, size=9),
                    ft.Text(actions_summary, color=SECONDARY, size=10),
                ], spacing=6),
                bgcolor=ELEVATED, border=ft.Border.all(1, BORDER), border_radius=9,
                padding=ft.Padding.all(13), width=940,
            ))
        return cards

    def _show_capabilities(self, e=None):
        self.active_page = "capabilities"
        self._set([
            self._page_heading("Capabilities", "What Chimera can do out of the box, plus optional specialist applications."),
            self._page_heading("Built-in toolkit", f"{len(BUILTIN_CAPABILITIES)} first-party capability groups are registered with the app.", eyebrow="READY WITHOUT EXTRA INSTALLS"),
            *self._builtin_capability_cards(),
            self._page_heading("Capability review queue", "Custom proposals stay separate from built-in tools. Nothing is silently promoted or marked accepted.", eyebrow="CUSTOM CAPABILITIES"),
            self._candidate_list(),
        ])


    def _show_workflow_studio(self, e=None):
        self.active_page = "workflow"
        try:
            runs = AuditStore(self.registry.data_dir / "chimera.db").workflow_runs()
            error = None
        except Exception as exc:
            runs, error = [], str(exc)
        cards = []
        for run_id, summary, started, finished, status, candidate, digest, run_error in runs[:12]:
            state = str(status or "unknown").replace("_", " ").title()
            cards.append(ft.Container(
                ft.Column([
                    ft.Row([
                        ft.Text(str(summary or "Workflow run"), color=TEXT, size=14, weight=ft.FontWeight.BOLD, expand=True),
                        ft.Text(state, color=FOCUS if status == "completed" else SECONDARY, size=10, weight=ft.FontWeight.BOLD),
                    ]),
                    ft.Text(f"Started {started or '—'}  ·  Finished {finished or '—'}", color=MUTED, size=10),
                    ft.Text(f"Run {str(run_id)[:16]}  ·  Candidate {candidate or '—'}", color=SECONDARY, size=10),
                    *([ft.Text(f"Result: {run_error}", color=ACCENT, size=11)] if run_error else []),
                ], spacing=7),
                bgcolor=ELEVATED, border=ft.Border.all(1, BORDER), border_radius=10,
                padding=ft.Padding.all(15), width=940,
            ))
        self._set([
            self._page_heading("Workflow studio", "Build and run governed, sequential work across Chimera capabilities. No external workflow service is required."),
            self._panel("Chimera workflow engine", "Start from a natural-language outcome in Chat. Chimera validates registered tools, checks dependencies, pauses for approval, and records run evidence.", [
                ft.Row([
                    self._button("OPEN CHAT WORKBENCH", lambda e: self._navigate(self._show_overview)),
                    ft.Text("Use “Run workflow” in the chat composer to begin.", color=SECONDARY, size=11),
                ], wrap=True, spacing=12),
            ]),
            self._page_heading("Recent workflow runs", "Persisted activity only. No placeholder or fabricated runs are shown.", eyebrow="WORKFLOW HISTORY"),
            *([ft.Text(error, color=ACCENT)] if error else []),
            *(cards or [ft.Container(
                ft.Text("No workflow runs recorded yet. Your first run will appear here with its real status and timestamps.", color=MUTED, size=12),
                bgcolor=SURFACE, border=ft.Border.all(1, BORDER), border_radius=10,
                padding=ft.Padding.all(18), width=940,
            )]),
        ])


    def _show_model_hub(self, e=None):
        self.active_page = "model"
        profiles = self.profile_store.list()
        self.hub_id = ft.TextField(label="Profile ID", hint_text="e.g. local-llama-3b", color=TEXT, border_color=BORDER, focused_border_color=FOCUS, expand=True)
        self.hub_provider = ft.TextField(label="Provider ID", hint_text="e.g. llama_cpp or openai", color=TEXT, border_color=BORDER, focused_border_color=FOCUS, expand=True)
        self.hub_model = ft.TextField(label="Model ID or GGUF path", hint_text="Model name or full local .gguf path", color=TEXT, border_color=BORDER, focused_border_color=FOCUS, expand=True)
        self.hub_url = ft.TextField(label="API base URL", hint_text="Required only for hosted API profiles", color=TEXT, border_color=BORDER, focused_border_color=FOCUS, expand=True)
        self.hub_secret = ft.TextField(label="API credential", hint_text="Used for this session; not stored in the profile", color=TEXT, border_color=BORDER, focused_border_color=FOCUS, password=True, can_reveal_password=True, expand=True)
        self.hub_tools = ft.Checkbox(label="Allow this provider to request registered tool calls", value=True)
        self.hub_dialect = ft.TextField(label="API format (optional)", hint_text="Auto-detect, or enter a supported API dialect", color=TEXT, border_color=BORDER, focused_border_color=FOCUS, expand=True)

        cards = []
        for profile in profiles:
            actions = ft.Row([
                ft.TextButton("Use in this chat", on_click=lambda e, pid=profile.profile_id: self._select_session_model(pid), style=ft.ButtonStyle(color=FOCUS)),
                ft.TextButton("Compatibility", on_click=lambda e, pid=profile.profile_id: self._run_compat_lab(pid), style=ft.ButtonStyle(color=FOCUS)),
                ft.TextButton("Make default", on_click=lambda e, pid=profile.profile_id: self._set_default_model(pid), style=ft.ButtonStyle(color=FOCUS)),
                ft.TextButton("Enable / disable", on_click=lambda e, pid=profile.profile_id: self._toggle_model(pid), style=ft.ButtonStyle(color=SECONDARY)),
                ft.TextButton("Remove", on_click=lambda e, pid=profile.profile_id: self._remove_model(pid), style=ft.ButtonStyle(color=ACCENT)),
            ], wrap=True, spacing=5)
            cards.append(ft.Container(
                ft.Column([
                    ft.Row([
                        ft.Text(profile.display_name or profile.model_id, color=TEXT, size=15, weight=ft.FontWeight.BOLD, expand=True),
                        ft.Container(ft.Text("HOSTED API" if profile.hosted else "LOCAL MODEL", color=FOCUS, size=9, weight=ft.FontWeight.BOLD),
                            bgcolor="#19130D", border=ft.Border.all(1, "#6A4A25"), border_radius=20,
                            padding=ft.Padding.symmetric(horizontal=10, vertical=6)),
                    ]),
                    ft.Text(f"Provider: {profile.provider_id}  ·  Format: {getattr(profile, 'dialect', None) or 'automatic'}", color=SECONDARY, size=11),
                    ft.Text(f"Tools: {'enabled' if profile.tool_calling else 'disabled'}  ·  Structured output: {profile.structured_output}  ·  State: {'enabled' if profile.enabled else 'disabled'}", color=MUTED, size=10),
                    actions,
                ], spacing=8),
                bgcolor=ELEVATED, border=ft.Border.all(1, BORDER), border_radius=10,
                padding=ft.Padding.all(15), width=940,
            ))

        form = ft.Container(
            ft.Column([
                ft.Text("Add a model profile", color=TEXT, size=17, weight=ft.FontWeight.BOLD),
                ft.Text("Local GGUF profiles run on this device. Hosted APIs are used only when you explicitly select that profile.", color=SECONDARY, size=11),
                ft.Row([self.hub_id, self.hub_provider], spacing=12),
                ft.Row([self.hub_model, self.hub_url], spacing=12),
                ft.Row([self.hub_secret, self.hub_dialect], spacing=12),
                self.hub_tools,
                ft.Row([
                    self._button("SAVE LOCAL PROFILE", lambda e: self._save_model_profile(False)),
                    self._button("SAVE API PROFILE", lambda e: self._save_model_profile(True)),
                    ft.TextButton("Test selected API", on_click=self._test_model_profile, style=ft.ButtonStyle(color=FOCUS)),
                ], wrap=True, spacing=9),
            ], spacing=11),
            bgcolor=SURFACE, border=ft.Border.all(1, BORDER), border_radius=12,
            padding=ft.Padding.all(18), width=940,
        )

        self._set([
            self._page_heading("Model hub", "Choose the reasoning provider for this conversation, then inspect its declared capabilities."),
            form,
            self._page_heading("Configured profiles", "Profiles are stored locally. Credentials are separate from profile metadata.", eyebrow="MODEL REGISTRY"),
            *(cards or [ft.Container(
                ft.Text("No model profiles configured yet. Add a local GGUF or an explicitly selected API profile above.", color=MUTED, size=12),
                bgcolor=SURFACE, border=ft.Border.all(1, BORDER), border_radius=10,
                padding=ft.Padding.all(18), width=940,
            )]),
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
        """Discover open Windows apps and expose specialist application connectors."""
        self.active_page = "applications"
        self.apps_search = ft.TextField(
            label="Search open applications", hint_text="Filter by executable or window title",
            color=TEXT, border_color=BORDER, focused_border_color=FOCUS,
            on_change=lambda e: self._render_computer_apps(), expand=True,
        )
        self._refresh_computer_apps()

    def _refresh_computer_apps(self, e=None):
        try:
            self._desktop_applications = self.agent_runtime.computer.backend.list_applications()
            self._desktop_inventory_error = None
        except Exception as exc:
            self._desktop_applications, self._desktop_inventory_error = [], str(exc)
        try:
            from .applications.registry import get_application_registry
            self._application_manifests = get_application_registry().discover_all()
        except Exception as exc:
            self._application_manifests = []
            self._connector_inventory_error = str(exc)
        self._render_computer_apps()

    def _render_computer_apps(self):
        applications = getattr(self, "_desktop_applications", [])
        query = (getattr(getattr(self, "apps_search", None), "value", "") or "").strip().casefold()
        visible = []
        for app in applications:
            executable = str(app.get("executable") or "")
            titles = [str(window.get("title") or "").strip() for window in app.get("windows", []) if isinstance(window, dict)]
            if query and query not in (executable + " " + " ".join(titles)).casefold():
                continue
            visible.append((app, titles))

        cards = []
        for app, titles in visible:
            pid, executable = app.get("pid"), app.get("executable")
            app_name = str(executable or "Application").replace("\\", "/").rsplit("/", 1)[-1]
            authorized = any(
                identity.owner_session_id == self.agent_session_id
                and identity.pid == pid
                and (identity.executable or "").casefold() == str(executable or "").casefold()
                and self.agent_runtime.computer._identity_is_current(identity)
                for identity in self.agent_runtime.computer.sessions.values()
            )
            action = (
                ft.Container(
                    ft.Text("Authorized for this conversation", color=FOCUS, size=10, weight=ft.FontWeight.BOLD),
                    bgcolor="#19130D", border=ft.Border.all(1, "#6A4A25"), border_radius=20,
                    padding=ft.Padding.symmetric(horizontal=10, vertical=7),
                )
                if authorized else self._button(
                    "AUTHORIZE THIS APP",
                    lambda e, process_id=pid, image=executable: self._authorize_computer_application(process_id, image),
                )
            )
            cards.append(ft.Container(
                ft.Column([
                    ft.Row([ft.Text(app_name, color=TEXT, size=15, weight=ft.FontWeight.BOLD, expand=True), ft.Text(f"PID {pid}", color=MUTED, size=9)]),
                    ft.Text("  ·  ".join(title for title in titles[:3] if title) or "Visible application window", color=SECONDARY, size=11),
                    ft.Text(str(executable or "Executable path unavailable"), color=MUTED, size=9),
                    action,
                ], spacing=8),
                bgcolor=SURFACE, border=ft.Border.all(1, BORDER), border_radius=10,
                padding=ft.Padding.all(15), width=940,
            ))

        count = len(applications)
        content = [
            self._page_heading("Applications", "Chimera discovers visible Windows applications at runtime. Authorize one exact running process before it can inspect the app's interface."),
            ft.Container(
                ft.Row([
                    ft.Column([
                        ft.Text(f"{count} open application{'s' if count != 1 else ''} discovered", color=TEXT, size=15, weight=ft.FontWeight.BOLD),
                        ft.Text("This list refreshes from live desktop windows; it is not a hard-coded integration catalogue.", color=SECONDARY, size=10),
                    ], expand=True, spacing=4),
                    self._button("REFRESH", self._refresh_computer_apps),
                ], spacing=12),
                bgcolor=ELEVATED, border=ft.Border.all(1, BORDER), border_radius=10, padding=ft.Padding.all(14), width=940,
            ),
            ft.Row([self.apps_search], spacing=10),
            self._page_heading("Specialist connectors", "These connectors are shipped with Chimera. Some need a separate native companion application.", eyebrow="APPLICATION ECOSYSTEM"),
            *self._connector_status_cards(),
            ft.Row([
                self._button("INSTALL OPTIONAL APPS & CONTROL DEPENDENCIES", self._launch_setup_helper),
                ft.Text("Windows setup helper · Winget optional", color=MUTED, size=9),
            ], wrap=True, spacing=10),
        ]
        if self._desktop_inventory_error:
            content.append(ft.Text(f"Application inventory unavailable: {self._desktop_inventory_error}", color=ACCENT, size=11))
        elif not applications:
            content.append(ft.Container(
                ft.Text("No titled application windows are visible right now. Open an application and refresh this view.", color=MUTED, size=12),
                bgcolor=SURFACE, border=ft.Border.all(1, BORDER), border_radius=10, padding=ft.Padding.all(18), width=940,
            ))
        elif not visible:
            content.append(ft.Text("No open applications match this search.", color=MUTED, size=12))
        else:
            content.extend(cards)
        content.append(ft.Text(
            "Authorization is scoped to this conversation and exact process identity. Fresh UI Automation observations are still required; global keyboard injection, blind focus changes and remote window closing remain disabled.",
            color=MUTED, size=10,
        ))
        self._set(content)

    def _launch_setup_helper(self, e=None):
        if os.name != "nt":
            return self._toast("The companion-app installer is available on Windows only.")
        base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
        script = base / "scripts" / "setup_windows.bat"
        if not script.is_file():
            return self._toast("Setup helper not found. Run scripts\\setup_windows.bat from the source checkout.")
        try:
            os.startfile(str(script))
        except Exception as exc:
            self._toast(f"Could not launch the Windows setup helper: {exc}")

    def _authorize_computer_application(self, pid, executable):
        try:
            identity = self.agent_runtime.computer.create(
                pid,
                executable,
                owner_session_id=self.agent_session_id,
            )
            self._toast(f"Authorized {Path(identity.executable).name} for this conversation.")
            self._refresh_computer_apps()
        except Exception as exc:
            self._toast(f"Application authorization failed: {exc}")

    def _show_runs(self, e=None):
        self.active_page = "runs"
        self._set([ft.Text("RUNS", size=18, color=TEXT), ft.Text("No executions recorded.", color=MUTED)])
    def _show_settings(self, e=None):
        self.active_page = "model"
        config = self.local_provider.config
        status = check_status(config)
        server = self.local_provider.server.status() if self.local_provider.server else {"state": "not selected"}
        state, detail, state_color = self._model_runtime_summary()
        tuning = ft.Container(
            ft.Column([
                ft.Text("Inference budget", color=FOCUS, size=12, weight=ft.FontWeight.BOLD),
                ft.Text("A 4K context is a good starting point for recent conversation evidence and the registered tool catalogue.", color=SECONDARY, size=10),
                ft.Row([self.context_field, self.max_tokens_field, self.threads_field, self.gpu_layers_field], wrap=True, spacing=10),
                ft.Row([
                    ft.TextButton("APPLY 4K STARTER PRESET", on_click=self._apply_balanced_preset, style=ft.ButtonStyle(color=ACCENT)),
                    ft.Text("4096 context · 512 output · 6 CPU threads · 20 GPU layers", color=MUTED, size=10),
                ], wrap=True),
            ], spacing=10),
            bgcolor=SURFACE, border=ft.Border.all(1, BORDER), border_radius=12, padding=ft.Padding.all(16),
        )
        runtime = ft.Container(
            ft.Column([
                ft.Row([ft.Text("LOCAL MODEL RUNTIME", color=TEXT, size=15, weight=ft.FontWeight.BOLD), ft.Container(expand=True), ft.Text(state, color=state_color, size=9, weight=ft.FontWeight.BOLD)]),
                ft.Text(f"{detail}\nMODEL  {config.model_path or 'NOT CONFIGURED'}\nSERVER  {config.server_path or 'NOT SELECTED'}\nCLI  {config.cli_path or 'NOT SELECTED'}\nSERVER STATE  {_upper(server.get('state'))}  ·  PID {server.get('pid') or '—'}  ·  {server.get('url') or 'no loopback endpoint'}", color=SECONDARY, size=10),
                ft.Text("Persistent mode uses only the selected llama-server. If it fails, the request fails. Chimera will not switch to llama-cli or a hosted model. CLI-only mode is explicit and loads the GGUF per request.", color=MUTED, size=10),
            ], spacing=9),
            bgcolor=ELEVATED, border=ft.Border.all(1, BORDER), border_radius=12, padding=ft.Padding.all(16),
        )
        quantization = ft.Container(
            ft.Column([
                ft.Text("MODEL / QUANTIZATION NOTES", color=FOCUS, size=10, weight=ft.FontWeight.BOLD),
                ft.Text("For a compact 3B local agent, Q4_K_M is the balanced starting point: good quality per GB, with room for context and KV cache. Q5_K_M can improve fidelity but needs more memory. Quantization is baked into the GGUF; Chimera does not rewrite or fetch model files.", color=SECONDARY, size=10),
                ft.Text("The preset is a tuning starting point, not a promise of fit. Lower GPU layers first if VRAM is tight; reduce context next if memory pressure persists.", color=MUTED, size=9),
            ], spacing=7),
            bgcolor=PANEL_WARM, border=ft.Border.all(1, "#3A302C"), border_radius=12, padding=ft.Padding.all(15),
        )
        self._set([
            self._page_heading("Model settings", "Configure your GGUF file, persistent llama-server, and local inference budget.", eyebrow="CHIMERA  /  MODEL BAY"),
            runtime,
            ft.Container(ft.Column([
                ft.Text("MODEL FILE", color=FOCUS, size=10, weight=ft.FontWeight.BOLD),
                ft.Row([self.model_path_field, ft.TextButton("BROWSE GGUF", on_click=lambda e: self.model_picker.pick_files(allowed_extensions=["gguf"]), style=ft.ButtonStyle(color=ACCENT))]),
                ft.Text("PERSISTENT SERVER", color=FOCUS, size=10, weight=ft.FontWeight.BOLD),
                ft.Row([self.server_path_field, ft.TextButton("BROWSE SERVER", on_click=lambda e: self.server_picker.pick_files(), style=ft.ButtonStyle(color=ACCENT))]),
                ft.Text("OPTIONAL CLI-ONLY RUNTIME", color=MUTED, size=9, weight=ft.FontWeight.BOLD),
                ft.Row([self.runtime_path_field, ft.TextButton("BROWSE CLI", on_click=lambda e: self.runtime_picker.pick_files(), style=ft.ButtonStyle(color=SECONDARY))]),
            ], spacing=9), bgcolor=SURFACE, border=ft.Border.all(1, BORDER), border_radius=12, padding=ft.Padding.all(16)),
            tuning, quantization,
            ft.Row([
                self._button("SAVE CONFIGURATION", self._save_model_config),
                ft.TextButton("LOAD MODEL NOW", on_click=self._start_local_server, style=ft.ButtonStyle(color=TEXT)),
                ft.TextButton("STOP MODEL", on_click=self._stop_local_server, style=ft.ButtonStyle(color=SECONDARY)),
            ], wrap=True, spacing=8),
            ft.Text(f"Configuration source: {config_source()}  ·  Data: {self.registry.data_dir}", color=MUTED, size=9),
        ])

    async def _start_local_server(self, e=None):
        if not self.local_provider.server:
            return self._toast("Select a llama-server executable and save the configuration first.")
        try:
            self.model_boot_status = "loading"
            self._refresh_runtime_indicators()
            await asyncio.to_thread(self.local_provider.server.start)
            self.model_boot_status = "ready"
            self._toast("Local model loaded and resident in the llama-server process.")
        except Exception as exc:
            self.model_boot_status = "failed"
            self._toast(f"Model load failed. No alternate runtime was selected: {exc}")
        self._show_settings()
    def _stop_local_server(self, e=None):
        self.local_provider.stop(); self._toast("Local model server stopped."); self._show_settings()
    def _model_picked(self, e):
        if e.files: self.model_path_field.value = e.files[0].path; self.page.update()
    def _runtime_picked(self, e):
        if e.files: self.runtime_path_field.value = e.files[0].path; self.page.update()
    def _server_picked(self, e):
        if e.files: self.server_path_field.value = e.files[0].path; self.page.update()
    def _apply_balanced_preset(self, e=None):
        self.context_field.value = "4096"
        self.max_tokens_field.value = "512"
        self.threads_field.value = "6"
        self.gpu_layers_field.value = "20"
        self._toast("Balanced 3B preset staged. Save it after checking the selected model and GPU headroom.")
        self.page.update()
    async def _warm_local_model(self):
        config = self.local_provider.config
        if not self.local_provider.server:
            self.model_boot_status = "cli_only" if config.cli_path else "missing"
            self._refresh_runtime_indicators()
            return
        if check_status(config).state != "ready":
            self.model_boot_status = "missing"
            self._refresh_runtime_indicators()
            return
        self.model_boot_status = "loading"
        self._refresh_runtime_indicators()
        try:
            await asyncio.to_thread(self.local_provider.server.start)
            self.model_boot_status = "ready"
        except Exception as exc:
            self.model_boot_status = "failed"
            self.last_turn_notice = f"Local model failed to load. No CLI or hosted fallback was attempted: {exc}"
        self._refresh_runtime_indicators()
    def _save_model_config(self, e=None):
        model = Path(self.model_path_field.value).expanduser() if self.model_path_field.value else None
        cli = self.runtime_path_field.value.strip() or None
        server = self.server_path_field.value.strip() or None
        try:
            context = int(self.context_field.value)
            max_tokens = int(self.max_tokens_field.value)
            threads = int(self.threads_field.value)
            gpu_layers = int(self.gpu_layers_field.value)
        except (TypeError, ValueError):
            return self._toast("Context, token, thread and GPU-layer settings must be integers.")
        if context < 512 or max_tokens < 1 or threads < 1 or gpu_layers < 0:
            return self._toast("Use context ≥ 512, tokens/threads ≥ 1, and GPU layers ≥ 0.")
        if model and model.suffix.lower() != ".gguf": return self._toast("Model must be an existing .gguf file.")
        if model and not model.is_file(): return self._toast("Selected model file does not exist.")
        if cli and not Path(cli).is_file(): return self._toast("Selected llama-cli executable does not exist.")
        if server and not Path(server).is_file(): return self._toast("Selected llama-server executable does not exist.")
        if server and cli:
            # Keeping both paths is useful for explicit diagnostics, but server wins
            # only because the operator explicitly configured it in this field.
            pass
        save_persisted_config(
            model, cli, server_path=server, context=context, max_tokens=max_tokens,
            threads=threads, gpu_layers=gpu_layers,
        )
        try:
            self.local_provider.stop()
        except Exception:
            pass
        self.local_provider = LocalModelProvider(config_from_env())
        self.agent_runtime.provider = self.local_provider
        self.local_verification = None
        self.model_boot_status = "checking"
        self._toast(f"Saved. Provider state: {_upper(check_status(self.local_provider.config).state)}")
        self._show_settings()
        self.page.run_task(self._warm_local_model)
    async def _test_local_inference(self, e=None):
        self._toast("Running genuine local inference..."); result = await asyncio.to_thread(LocalModelProvider(config_from_env()).plan, "Return a concise JSON TaskSpec for creating a deterministic local text utility.")
        self._toast(f"Inference {result.status}: {result.duration_ms} ms, {result.generated_tokens} generated tokens" if result.status == "ready" else f"Inference failed: {result.error or result.status}")
    def _toast(self, message): self.page.snack_bar = ft.SnackBar(ft.Text(message, color=TEXT), bgcolor=ELEVATED); self.page.snack_bar.open = True; self.page.update()

    def _show_runs(self, e=None):
        self.active_page = "runs"
        try:
            runs = AuditStore(self.registry.data_dir / "chimera.db").workflow_runs()
        except Exception as exc:
            return self._set([
                self._page_heading("Run history", "A record of real workflow and execution activity."),
                ft.Text(f"History unavailable: {exc}", color=ACCENT, size=11),
            ])

        execution_rows = []
        execution_root = self.registry.data_dir / "executions"
        if execution_root.is_dir():
            for record in sorted(execution_root.glob("*/execution.json")):
                try:
                    execution_rows.append(json.loads(record.read_text(encoding="utf-8")))
                except (OSError, json.JSONDecodeError):
                    execution_rows.append({"status": "unavailable", "error": f"Malformed execution record: {record.name}"})

        cards = []
        for run_id, summary, started, finished, status, candidate, digest, error in runs:
            state = str(status or "unknown")
            cards.append(ft.Container(
                ft.Column([
                    ft.Row([
                        ft.Text(str(summary or "Workflow run"), color=TEXT, size=14, weight=ft.FontWeight.BOLD, expand=True),
                        ft.Text(state.replace("_", " ").upper(), color=FOCUS if state == "completed" else SECONDARY, size=9, weight=ft.FontWeight.BOLD),
                    ]),
                    ft.Text(f"Run {str(run_id)[:16]}", color=MUTED, size=9),
                    ft.Text(f"Started  {started or '—'}    ·    Finished  {finished or '—'}", color=SECONDARY, size=10),
                    ft.Text(f"Candidate  {candidate or '—'}    ·    Digest  {digest or '—'}", color=MUTED, size=9),
                    *([ft.Text(f"Result: {error}", color=ACCENT, size=10)] if error else []),
                ], spacing=7),
                bgcolor=SURFACE, border=ft.Border.all(1, BORDER), border_radius=10, padding=ft.Padding.all(16), width=940,
            ))
        for item in execution_rows:
            status = _upper(item.get("status"))
            cards.append(ft.Container(
                ft.Column([
                    ft.Row([
                        ft.Text(str(item.get("objective") or "Execution run"), color=TEXT, size=14, weight=ft.FontWeight.BOLD, expand=True),
                        ft.Text(status, color=FOCUS if status == "COMPLETED" else SECONDARY, size=9, weight=ft.FontWeight.BOLD),
                    ]),
                    ft.Text(f"Task {str(item.get('task_id', 'execution'))[:16]}", color=MUTED, size=9),
                    ft.Text(f"Artifact  {item.get('artifact') or '—'}", color=SECONDARY, size=10),
                    *([ft.Text(f"Error: {item.get('error')}", color=ACCENT, size=10)] if item.get("error") else []),
                ], spacing=7),
                bgcolor=SURFACE, border=ft.Border.all(1, BORDER), border_radius=10, padding=ft.Padding.all(16), width=940,
            ))
        self._set([
            self._page_heading("Run history", "Persisted workflow and execution activity. This view never fabricates progress or completion."),
            *(cards or [ft.Container(
                ft.Column([
                    ft.Text("No runs recorded yet", color=TEXT, size=15, weight=ft.FontWeight.BOLD),
                    ft.Text("Start a task or workflow from Chat. Its actual status and evidence will appear here.", color=MUTED, size=11),
                ], spacing=6),
                bgcolor=SURFACE, border=ft.Border.all(1, BORDER), border_radius=10, padding=ft.Padding.all(18), width=940,
            )]),
        ])

    def _show_goals(self, e=None):
        self.active_page = "goals"
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
        self.goal_tags_field = ft.TextField(
            label="TAGS (COMMA-SEPARATED)", color=TEXT, border_color=BORDER,
            focused_border_color=FOCUS, hint_text="e.g. engineering, research",
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
                self.goal_tags_field,
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
            tags = [
                item.strip() for item in (self.goal_tags_field.value or "").split(",")
                if item.strip()
            ]
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
            "active": "#E5B35A",
            "paused": "#BFA16A",
            "completed": "#E5B35A",
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
                ft.TextButton("EDIT", on_click=lambda e, gid=goal.goal_id: self._edit_goal(gid), style=ft.ButtonStyle(color=SECONDARY)),
                ft.TextButton("PAUSE", on_click=lambda e, gid=goal.goal_id: self._goal_action(gid, "paused"), style=ft.ButtonStyle(color=SECONDARY)),
                ft.TextButton("COMPLETE", on_click=lambda e, gid=goal.goal_id: self._goal_action(gid, "completed"), style=ft.ButtonStyle(color=ACCENT)),
                ft.TextButton("ARCHIVE", on_click=lambda e, gid=goal.goal_id: self._goal_action(gid, "archived"), style=ft.ButtonStyle(color=MUTED)),
            ])
        elif goal.status == "paused":
            actions.extend([
                ft.TextButton("EDIT", on_click=lambda e, gid=goal.goal_id: self._edit_goal(gid), style=ft.ButtonStyle(color=SECONDARY)),
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

    def _edit_goal(self, goal_id):
        goal = self.goal_store.get(goal_id)
        if goal is None:
            self._toast("Goal no longer exists.")
            self._show_goals()
            return
        if goal.status in {"completed", "archived"}:
            self._toast("Reopen this goal before editing it.")
            return

        title_field = ft.TextField(label="GOAL / OUTCOME", value=goal.title, color=TEXT, border_color=BORDER)
        description_field = ft.TextField(
            label="DESCRIPTION / CONTEXT", value=goal.description, multiline=True,
            min_lines=2, max_lines=4, color=TEXT, border_color=BORDER,
        )
        priority_field = ft.TextField(label="PRIORITY", value=goal.priority, color=TEXT, border_color=BORDER)
        project_field = ft.TextField(label="PROJECT", value=goal.project, color=TEXT, border_color=BORDER)
        criteria_field = ft.TextField(
            label="SUCCESS CRITERIA (ONE PER LINE)",
            value="\n".join(goal.success_criteria), multiline=True,
            min_lines=2, max_lines=4, color=TEXT, border_color=BORDER,
        )
        tags_field = ft.TextField(
            label="TAGS (COMMA-SEPARATED)", value=", ".join(goal.tags),
            color=TEXT, border_color=BORDER,
        )

        def save(_=None):
            try:
                self.goal_store.update(
                    goal_id,
                    title=title_field.value or "",
                    description=description_field.value or "",
                    priority=(priority_field.value or "").strip().lower(),
                    project=project_field.value or "",
                    success_criteria=[
                        item.strip() for item in (criteria_field.value or "").splitlines()
                        if item.strip()
                    ],
                    tags=[
                        item.strip() for item in (tags_field.value or "").split(",")
                        if item.strip()
                    ],
                )
                dialog.open = False
                self.page.update()
                self._toast("Goal updated.")
                self._show_goals()
            except Exception as exc:
                self._toast(f"Goal update failed: {exc}")

        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("EDIT OPERATOR GOAL"),
            content=ft.Column([
                title_field, description_field, priority_field,
                project_field, criteria_field, tags_field,
            ], tight=True, spacing=9, scroll=ft.ScrollMode.AUTO),
            actions=[
                ft.TextButton("CANCEL", on_click=lambda e: (setattr(dialog, "open", False), self.page.update())),
                ft.TextButton("SAVE CHANGES", on_click=save, style=ft.ButtonStyle(color=ACCENT)),
            ],
        )
        self.page.show_dialog(dialog)

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
                    if needs_evidence:
                        reason = (note_field.value or "").strip() or None
                    else:
                        reason = (evidence_field.value or "").strip()
                        additional = (note_field.value or "").strip()
                        if additional:
                            reason += "\\n\\nAdditional context: " + additional
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

    def _active_model_label(self):
        try:
            selection = self.agent_store.session_model(self.agent_session_id)
            profile_id = selection.get("profile_id")
            if profile_id:
                profile = next((item for item in self.profile_store.list() if item.profile_id == profile_id), None)
                if profile:
                    return profile.display_name or Path(profile.model_path or profile.model_id).name
            path = self.local_provider.config.model_path
            return path.name if path else "No local model selected"
        except Exception:
            return "Model configuration unavailable"

    def _refresh_runtime_indicators(self):
        local_state = self._runtime_state().replace("RUNTIME: ", "")
        model_state, _, model_color = self._model_runtime_summary()
        if getattr(self, "runtime_status_text", None) is not None:
            self.runtime_status_text.value = f"LOCAL RUNTIME  /  {local_state}"
            try:
                self.runtime_status_text.update()
            except Exception:
                pass
        if getattr(self, "chat_model_state_text", None) is not None:
            self.chat_model_state_text.value = model_state
            self.chat_model_state_text.color = model_color
            try:
                self.chat_model_state_text.update()
            except Exception:
                pass

    def _model_runtime_summary(self):
        provider = self.local_provider
        config = provider.config
        if not config.model_path:
            return "MODEL NOT SET", "Choose a local GGUF in the Model Bay.", ACCENT
        base = check_status(config)
        if base.state != "ready":
            return "RUNTIME OFFLINE", base.detail, ACCENT
        if provider.server:
            try:
                info = provider.server.status()
                state = info.get("state", "unknown")
                if state == "ready":
                    return "MODEL LOADED", "Persistent llama-server · model resident in memory", FOCUS
                if state in {"starting", "model_loading"} or self.model_boot_status == "loading":
                    return "LOADING MODEL", "Loading local GGUF into the persistent server", ACCENT
                if state == "busy":
                    return "MODEL WORKING", "Local inference is in progress", FOCUS
                if state == "failed" or self.model_boot_status == "failed":
                    return "LOAD FAILED", "Server failed. No alternate model runtime will be selected.", ACCENT
                return "SERVER READY", "Persistent mode · model loads on first request", SECONDARY
            except Exception as exc:
                return "SERVER ERROR", str(exc), ACCENT
        return "CLI / ONE-SHOT", "Explicit CLI mode · GGUF is loaded for each request", SECONDARY

    def _chat_sidebar(self):
        active_page = getattr(self, "active_page", "chat")
        sessions = self.agent_store.list_sessions(limit=6)
        recent = []
        for item in sessions:
            sid = item["session_id"]
            label = self._session_title(sid)
            is_current = sid == self.agent_session_id
            recent.append(
                ft.Container(
                    ft.Row([
                        ft.Text("▸", color=FOCUS if is_current else MUTED, size=11),
                        ft.Text(label, color=TEXT if is_current else SECONDARY, size=10, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                    ], spacing=8),
                    bgcolor=ELEVATED if is_current else None,
                    border=ft.Border.all(1, "#4A3A26") if is_current else None,
                    border_radius=7,
                    padding=ft.Padding.symmetric(horizontal=9, vertical=9),
                    on_click=lambda e, session_id=sid: self._resume_agent_session(session_id),
                )
            )
        return ft.Container(
            ft.Column([
                ft.Container(
                    ft.Row([
                        ft.Container(ft.Text("✳", color=FOCUS, size=19, weight=ft.FontWeight.BOLD), bgcolor="#18130E", border_radius=8, padding=ft.Padding.all(8)),
                        ft.Column([
                            ft.Text("CHIMERA", color=TEXT, size=18, weight=ft.FontWeight.BOLD, font_family=getattr(self, "logo_font_family", "Segoe UI")),
                            ft.Text("LOCAL WORKBENCH", color=MUTED, size=8),
                        ], spacing=2),
                    ], spacing=10),
                    tooltip="Return to chat",
                    on_click=lambda e: self._navigate(self._show_overview),
                ),
                ft.Container(self._button("＋   NEW TASK", lambda e: self._new_agent_session()), width=208),
                ft.Text("WORKSPACE", color=MUTED, size=8, weight=ft.FontWeight.BOLD),
                self._nav_item("◉", "Chat workbench", self._show_overview, active_page == "chat"),
                self._nav_item("⌘", "Model bay", self._show_model_hub, active_page == "model"),
                self._nav_item("▧", "Applications", self._show_computer_apps, active_page == "applications"),
                self._nav_item("⌁", "Workflow studio", self._show_workflow_studio, active_page == "workflow"),
                self._nav_item("⬡", "Capabilities", self._show_capabilities, active_page == "capabilities"),
                self._nav_item("◎", "Goal ledger", self._show_goals, active_page == "goals"),
                self._nav_item("◷", "Run history", self._show_runs, active_page == "runs"),
                ft.Divider(height=1, color=BORDER),
                ft.Row([
                    ft.Text("RECENT THREADS", color=MUTED, size=8, weight=ft.FontWeight.BOLD),
                    ft.TextButton("＋", on_click=lambda e: self._new_agent_session(), style=ft.ButtonStyle(color=ACCENT)),
                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                ft.Column(recent or [ft.Text("No threads yet. Start with one clear outcome.", color=MUTED, size=9)], spacing=3, scroll=ft.ScrollMode.AUTO, expand=True),

            ], spacing=9, expand=True),
            width=238, bgcolor="#070707", padding=ft.Padding.all(14),
            border=ft.Border.only(right=ft.BorderSide(1, BORDER)),
        )

    def _chat_bubble(self, role, content, created_at=None):
        if role == "user":
            bubble = ft.Container(
                ft.Column([
                    ft.Row([
                        ft.Text("YOU", color=FOCUS, size=8, weight=ft.FontWeight.BOLD),
                        ft.Container(expand=True),
                        ft.Text("REQUEST", color=MUTED, size=8),
                    ]),
                    ft.Text(content, color=TEXT, size=13),
                ], spacing=8),
                bgcolor="#121212", border=ft.Border.all(1, "#4A3A26"),
                border_radius=ft.BorderRadius.only(top_left=14, top_right=14, bottom_left=14, bottom_right=3),
                padding=ft.Padding.all(16), width=690,
            )
            return ft.Row([bubble], alignment=ft.MainAxisAlignment.END)

        bubble = ft.Container(
            ft.Column([
                ft.Row([
                    ft.Container(ft.Text("✳", color=FOCUS, size=13), bgcolor="#18130E", border_radius=6, padding=ft.Padding.all(5)),
                    ft.Text("CHIMERA", color=TEXT, size=9, weight=ft.FontWeight.BOLD),
                    ft.Text("LOCAL REASONER", color=MUTED, size=8),
                    ft.Container(expand=True),
                    ft.Text("RESPONSE", color=ACCENT, size=8, weight=ft.FontWeight.BOLD),
                ], spacing=7),
                ft.Text(content, color=SECONDARY, size=13),
            ], spacing=10),
            bgcolor=SURFACE, border=ft.Border.only(left=ft.BorderSide(2, FOCUS)),
            border_radius=ft.BorderRadius.only(top_left=3, top_right=12, bottom_left=12, bottom_right=12),
            padding=ft.Padding.all(17), width=760,
        )
        return ft.Row([bubble], alignment=ft.MainAxisAlignment.START)

    def _tool_card(self, content):
        try:
            payload = json.loads(content)
            tool = payload.get("tool") or "Tool operation"
            status = str(payload.get("status") or "recorded").upper()
            result = payload.get("result")
            if tool == "computer.list_applications" and isinstance(result, list):
                summary = []
                for app in result[:10]:
                    if not isinstance(app, dict):
                        continue
                    name = str(app.get("executable") or "Application").replace("\\", "/").rsplit("/", 1)[-1]
                    titles = [str(window.get("title") or "").strip() for window in app.get("windows", [])[:2] if isinstance(window, dict)]
                    summary.append(f"{name}  ·  {', '.join(value for value in titles if value) or 'visible window'}")
                detail = "\\n".join(summary) or "No user-facing applications found."
            else:
                detail = json.dumps(result, ensure_ascii=False, indent=2, default=str)[:900] if result is not None else (payload.get("error") or "")
        except Exception:
            tool, status, detail = "Tool event", "RECORDED", content[:1000]
        color = "#E5B35A" if status == "COMPLETED" else "#BFA16A" if status in {"AWAITING_APPROVAL", "RECORDED"} else "#F17857"
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
            bgcolor="#070707", border=ft.Border.all(1, BORDER),
            border_radius=8, padding=ft.Padding.all(12), width=700,
        )

    def _conversation_content(self):
        self.live_status_text = None
        content = []
        workspace_path = getattr(self, "workspace_path", None) or default_workspace_root()
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
                        ft.Text("A local-first agent for thinking, building and using your tools.", color=SECONDARY, size=13),
                        ft.Text(f"Chimera can inspect files on {workspace_path}. New files require Build Mode and your approval.", color=MUTED, size=11, text_align=ft.TextAlign.CENTER),
                        ft.Row(chips, wrap=True, spacing=8),
                    ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=16),
                    alignment=ft.Alignment(0, 0), expand=True,
                    padding=ft.Padding.symmetric(horizontal=20, vertical=45),
                )
            )

        if self.task_busy:
            self.live_status_text = ft.Text(
                self._live_status_label(), color=SECONDARY, size=11
            )
            content.append(
                ft.Container(
                    ft.Row([
                        ft.ProgressRing(width=15, height=15, stroke_width=2, color=ACCENT),
                        ft.Column([
                            ft.Text("CHIMERA IS WORKING", color=ACCENT, size=8, weight=ft.FontWeight.BOLD),
                            self.live_status_text,
                        ], spacing=4),
                    ], spacing=12),
                    bgcolor=ELEVATED, border=ft.Border.all(1, BORDER),
                    border_radius=9, padding=ft.Padding.all(13), width=700,
                )
            )

        if self.last_turn_notice:
            content.append(
                ft.Container(
                    ft.Text(self.last_turn_notice, color="#F09A80", size=11),
                    bgcolor="#190C0A", border=ft.Border.all(1, "#5D3028"),
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
                            self._button("APPROVE THIS ACTION", self._approve_agent_tool_yes),
                            ft.TextButton("Reject", on_click=self._approve_agent_tool_no, style=ft.ButtonStyle(color=SECONDARY)),
                        ], spacing=10),
                    ], spacing=9),
                    bgcolor="#120D08", border=ft.Border.all(1, ACCENT),
                    border_radius=10, padding=ft.Padding.all(15), width=720,
                )
            )
        return content

    def _live_status_label(self):
        labels = {
            "model.request.started": "Local model is reasoning",
            "tool.call.requested": "Preparing {tool}",
            "tool.started": "Running {tool}",
            "tool.completed": "Completed {tool}",
            "tool.failed": "Could not complete {tool}",
            "assistant.message.completed": "Preparing the response",
            "turn.completed": "Finishing the response",
        }
        for event_type, payload in reversed(self.live_events):
            template = labels.get(event_type)
            if template:
                return template.format(tool=payload.get("tool", "tool operation"))
        return "Sending the request to your local model…"

    def _refresh_chat_feed(self):
        if getattr(self, "current_view", "chat") != "chat":
            return
        if getattr(self, "chat_feed", None) is None:
            self._show_overview()
            return
        # Keep the same scrollable control mounted. Flet's auto-scroll pins only
        # while the operator is at the end; scrolling up suspends that pin.
        self.chat_feed.controls = self._conversation_content()
        self.chat_feed.update()

    def _sync_chat_controls(self):
        if self.task_field is not None:
            self.task_field.disabled = self.task_busy
            try:
                self.task_field.update()
            except Exception:
                pass
        if self.send_button is not None:
            self.send_button.visible = not self.task_busy
            try:
                self.send_button.update()
            except Exception:
                pass
        if self.stop_button is not None:
            self.stop_button.visible = self.task_busy
            try:
                self.stop_button.update()
            except Exception:
                pass

    def _show_overview(self, e=None):
        self.active_page = "chat"
        self.current_view = "chat"
        if getattr(self, "workspace_path", None) is None:
            self.workspace_path = validate_workspace_path(default_workspace_root())
        session = self.agent_store.session(self.agent_session_id) or {}
        session_mode = session.get("mode", "plan")
        session_title = self._session_title(self.agent_session_id)
        model_label = self._active_model_label()
        model_state, model_detail, model_color = self._model_runtime_summary()
        workspace_label = str(self.workspace_path)
        self.chat_model_state_text = ft.Text(
            model_state, color=model_color, size=8, weight=ft.FontWeight.BOLD
        )

        if not hasattr(self, "task_field") or self.task_field is None:
            self.task_field = ft.TextField(
                multiline=True, min_lines=3, max_lines=8,
                hint_text="Describe the outcome. Chimera will work through it with you...",
                hint_style=ft.TextStyle(color=MUTED), bgcolor="#101010", color=TEXT,
                border_color=BORDER, focused_border_color=FOCUS, border_radius=11,
                expand=True, text_size=14,
            )
        self.task_field.disabled = self.task_busy

        mode_badge = ft.Container(
            ft.Text(f"{session_mode.upper()} MODE", color=FOCUS if session_mode == "build" else SECONDARY, size=8, weight=ft.FontWeight.BOLD),
            bgcolor="#19130D" if session_mode == "build" else ELEVATED,
            border=ft.Border.all(1, "#6A4A25" if session_mode == "build" else BORDER),
            border_radius=20, padding=ft.Padding.symmetric(horizontal=11, vertical=7),
        )
        header = ft.Container(
            ft.Row([
                ft.Column([
                    ft.Row([
                        ft.Text("LOCAL COMPUTER", color=MUTED, size=8, weight=ft.FontWeight.BOLD),
                        ft.Text(" / ", color=ACCENT, size=8),
                        ft.Text(self.workspace_path.drive or self.workspace_path.anchor, color=SECONDARY, size=8, weight=ft.FontWeight.BOLD),
                    ], spacing=4),
                    ft.Text(session_title, color=TEXT, size=19, weight=ft.FontWeight.BOLD, max_lines=1),
                    ft.Text(workspace_label, color=MUTED, size=9, max_lines=1),
                ], spacing=5, expand=True),
                ft.Container(
                    ft.Column([
                        ft.Row([
                            ft.Container(width=7, height=7, bgcolor=model_color, border_radius=8),
                            self.chat_model_state_text,
                        ], spacing=6),
                        ft.Text(model_label, color=TEXT, size=9, max_lines=1),
                    ], spacing=4),
                    bgcolor=SURFACE, border=ft.Border.all(1, BORDER), border_radius=9,
                    padding=ft.Padding.symmetric(horizontal=12, vertical=8),
                ),
                ft.Column([
                    ft.Row([
                        mode_badge,
                        ft.TextButton(
                            "SWITCH TO BUILD" if session_mode == "plan" else "SWITCH TO PLAN",
                            on_click=self._toggle_agent_mode, disabled=self.task_busy,
                            style=ft.ButtonStyle(color=ACCENT, padding=ft.Padding.symmetric(horizontal=8, vertical=6)),
                        ),
                    ], spacing=4),
                    ft.Text("READ-ONLY" if session_mode == "plan" else "APPROVAL-GATED", color=MUTED, size=8, text_align=ft.TextAlign.RIGHT),
                ], spacing=2, horizontal_alignment=ft.CrossAxisAlignment.END),
            ], spacing=14, alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            height=92, padding=ft.Padding.symmetric(horizontal=24, vertical=13),
            border=ft.Border.only(bottom=ft.BorderSide(1, BORDER)), bgcolor=BG,
        )

        self.send_button = self._button("SEND  ↑", self._send_conversation)
        self.stop_button = self._button("STOP  ■", self._cancel_conversation)
        self.send_button.visible = not self.task_busy
        self.stop_button.visible = self.task_busy

        composer = ft.Container(
            ft.Column([
                ft.Row([
                    ft.Text("NEW MESSAGE", color=FOCUS, size=8, weight=ft.FontWeight.BOLD),
                    ft.Text("CONTINUOUS THREAD", color=MUTED, size=8),
                    ft.Container(expand=True),
                    ft.Text("LOCAL-FIRST  /  NO SILENT FALLBACK", color=FOCUS, size=8),
                ], spacing=10),
                self.task_field,
                ft.Row([
                    ft.Text(f"FILE SCOPE  {self.workspace_path}", color=MUTED, size=8),
                    ft.TextButton("RUN WORKFLOW", on_click=self._run_multi_capability_workflow, disabled=self.task_busy, style=ft.ButtonStyle(color=SECONDARY, padding=ft.Padding.symmetric(horizontal=8, vertical=7))),
                    ft.Container(expand=True),
                    ft.Text("ENTER A GOAL  ·  KEEP THE THREAD GOING", color=MUTED, size=8),
                    self.send_button,
                    self.stop_button,
                ], spacing=6, alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            ], spacing=10),
            bgcolor="#0D0D0D", border=ft.Border.all(1, "#3A3025"), border_radius=13,
            padding=ft.Padding.all(14), margin=ft.Margin.symmetric(horizontal=22, vertical=13),
        )

        if getattr(self, "chat_feed", None) is None:
            self.chat_feed = ft.Column(
                self._conversation_content(), expand=True,
                scroll=ft.ScrollMode.AUTO, auto_scroll=True, spacing=17,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            )
        else:
            self.chat_feed.controls = self._conversation_content()
        feed = ft.Container(
            self.chat_feed,
            expand=True, padding=ft.Padding.symmetric(horizontal=22, vertical=20),
            bgcolor=BG,
        )
        center = ft.Column([header, feed, composer], expand=True, spacing=0)
        self.body.padding = ft.Padding.all(0)
        self.body.content = ft.Row([
            self._chat_sidebar(),
            ft.Container(center, expand=True, bgcolor=BG),
        ], expand=True, spacing=0)
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
        self.conversation_messages.append(("user", content))
        self.task_field.value = ""
        self._sync_chat_controls()
        self._refresh_chat_feed()
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
            self._sync_chat_controls()
            self._refresh_chat_feed()
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
        self._sync_chat_controls()
        self._refresh_chat_feed()
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
            self._sync_chat_controls()
            self._refresh_chat_feed()

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
        if self.current_view == "chat" and self.task_busy:
            try:
                self.page.run_task(self._update_live_status)
            except Exception:
                pass

    async def _update_live_status(self):
        if self.current_view != "chat" or not self.task_busy or self.live_status_text is None:
            return
        self.live_status_text.value = self._live_status_label()
        try:
            self.live_status_text.update()
        except Exception:
            pass
    async def _cancel_conversation(self, e=None):
        if self.task_busy:
            self.agent_runtime.cancel(self.agent_session_id)
            self._toast("Cancellation requested; the active model/tool boundary will stop safely.")
    async def _approve_agent_tool_yes(self, e=None):
        await self._approve_agent_tool(True)

    async def _approve_agent_tool_no(self, e=None):
        await self._approve_agent_tool(False)

    async def _approve_agent_tool(self, approved):
        if self.task_busy: return
        self.task_busy = True
        self._sync_chat_controls()
        self._refresh_chat_feed()
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
            self.task_busy = False
            self._sync_chat_controls()
            self._refresh_chat_feed()
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
        self.workspace_path = validate_workspace_path(default_workspace_root())
        # The folder-picker workflow has been retired. Migrate older threads
        # from their former project subfolder to the default system-drive scope.
        previous_workspace = session.get("workspace")
        if not previous_workspace or Path(previous_workspace).expanduser().resolve() != self.workspace_path:
            self.agent_store.set_workspace(session_id, self.workspace_path)
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
