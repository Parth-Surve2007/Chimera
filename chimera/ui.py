"""Monochrome Flet command console for Chimera."""
import sys, asyncio
from pathlib import Path
from .review import ReviewService
from .local_model import config_from_env, check_status, save_persisted_config, LocalModelProvider, load_persisted_config, config_data_dir
try: import flet as ft
except ImportError: ft = None

BG, SURFACE, ELEVATED, BORDER = "#050505", "#0B0B0B", "#151515", "#242424"
TEXT, SECONDARY, MUTED, DISABLED, FOCUS = "#F2F2F2", "#A0A0A0", "#686868", "#444444", "#777777"
ACCENT = FOCUS

def launch(registry):
    if ft is None: raise RuntimeError("Flet is not installed; run python -m pip install flet")
    return ft.run(lambda page: MainWindow(page, registry))

class MainWindow:
    def __init__(self, page, registry):
        self.page, self.registry, self.review = page, registry, ReviewService(registry.data_dir); self.drawer_open = False; self.selected = None
        self.model_picker = ft.FilePicker(on_result=self._model_picked); self.runtime_picker = ft.FilePicker(on_result=self._runtime_picked); page.services.extend([self.model_picker, self.runtime_picker])
        self.model_path_field = ft.TextField(label="GGUF MODEL PATH", color=TEXT, border_color=BORDER, value=str(config_from_env().model_path or ""), expand=True)
        self.runtime_path_field = ft.TextField(label="LLAMA.CPP EXECUTABLE", color=TEXT, border_color=BORDER, value=str(config_from_env().cli_path or ""), expand=True)
        page.title = "Chimera"; page.bgcolor = BG; page.padding = 0; page.window.width = 1200; page.window.height = 760; page.window.min_width = 860; page.window.min_height = 560
        font = Path(__file__).resolve().parent.parent / "fonts" / "ec-dots-life-font" / "EcDotsLifeRegular-AydL.ttf"
        page.fonts = {"ChimeraDots": str(font)} if font.exists() else {}
        self.body = ft.Container(expand=True, padding=ft.Padding.symmetric(horizontal=48, vertical=28)); self.drawer = self._drawer(); self._show_overview()
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
        for label, handler in [("OVERVIEW", self._show_overview), ("CAPABILITIES", self._show_capabilities), ("RUNS", self._show_runs), ("SETTINGS", self._show_settings)]:
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
    def _candidate_list(self):
        try: candidates = self.review.list()
        except Exception as exc: return ft.Text(str(exc), color=TEXT)
        if not candidates: return ft.Text("No persisted candidates.", color=MUTED)
        return ft.Column([ft.Container(ft.Row([ft.Column([ft.Text(c.name, color=TEXT), ft.Text(c.candidate_id, color=MUTED, size=10)], expand=True), ft.Text(c.status.upper(), color=SECONDARY, size=10), ft.TextButton("INSPECT", on_click=lambda e, c=c: self._inspect(c), style=ft.ButtonStyle(color=TEXT))]), padding=ft.Padding.symmetric(vertical=12), border=ft.Border.only(bottom=ft.BorderSide(1, BORDER))) for c in candidates], spacing=0)
    def _inspect(self, c):
        self.selected = c; self._set([ft.Text(f"CANDIDATE / {c.candidate_id}", size=18, color=TEXT), ft.Text(f"{c.name}\nSTATE: {c.status}\nTASK: {c.task}\nMANIFEST: {c.digest or 'UNVERIFIED'}\nACCEPTANCE APPROVAL: {c.acceptance_approved}\n\nStatic verification is not behavioral verification. Candidate code is never executed during review.", color=SECONDARY), ft.Row([ft.TextButton("VERIFY", on_click=lambda e: self._action(lambda: self.review.verify(c.candidate_id)), style=ft.ButtonStyle(color=TEXT)), ft.TextButton("APPROVE ACCEPTANCE", on_click=lambda e: self._action(lambda: self.review.approve_acceptance(c.candidate_id)), style=ft.ButtonStyle(color=TEXT)), ft.TextButton("ACCEPT", on_click=lambda e: self._action(lambda: self.review.accept(c.candidate_id), True), style=ft.ButtonStyle(color=TEXT))])])
    def _action(self, action, refresh=False):
        try: action(); self._toast("Completed through ReviewService."); self._show_capabilities() if refresh else None
        except Exception as exc: self._toast(f"Action failed: {exc}")
    def _show_runs(self, e=None): self._set([ft.Text("RUNS", size=18, color=TEXT), ft.Text("No executions recorded.", color=MUTED)])
    def _show_settings(self, e=None):
        c = config_from_env(); s = check_status(c); self._set([ft.Text("SETTINGS", size=18, color=TEXT), ft.Text(f"LOCAL RUNTIME: {s.state.upper()}\nMODEL: {c.model_path or 'NOT CONFIGURED'}\nEXECUTABLE: {c.cli_path or 'NOT CONFIGURED'}\nCONTEXT: {c.context}  TOKENS: {c.max_tokens}  THREADS: {c.threads}  GPU LAYERS: {c.gpu_layers}\n\nDATA: {self.registry.data_dir}\nEnvironment variables override persisted model_config.json.", color=SECONDARY), ft.Row([self.model_path_field, ft.TextButton("SELECT GGUF", on_click=lambda e: self.model_picker.pick_files(allowed_extensions=["gguf"]))]), ft.Row([self.runtime_path_field, ft.TextButton("SELECT RUNTIME", on_click=lambda e: self.runtime_picker.pick_files())]), ft.Row([self._button("SAVE + VALIDATE", self._save_model_config), ft.TextButton("TEST LOCAL INFERENCE", on_click=self._test_local_inference, style=ft.ButtonStyle(color=TEXT))]), ft.Text("Select existing local files only. Models are never downloaded or bundled.", color=MUTED, size=11)])
    def _model_picked(self, e):
        if e.files: self.model_path_field.value = e.files[0].path; self.page.update()
    def _runtime_picked(self, e):
        if e.files: self.runtime_path_field.value = e.files[0].path; self.page.update()
    def _save_model_config(self, e=None):
        model = Path(self.model_path_field.value) if self.model_path_field.value else None; runtime = self.runtime_path_field.value or None
        if model and model.suffix.lower() != ".gguf": return self._toast("Model must be an existing .gguf file.")
        if model and not model.is_file(): return self._toast("Selected model file does not exist.")
        if runtime and not Path(runtime).is_file(): return self._toast("Selected llama.cpp executable does not exist.")
        save_persisted_config(model, runtime); self._toast(f"Saved. Provider state: {check_status(config_from_env()).state.upper()}"); self._show_settings()
    async def _test_local_inference(self, e=None):
        self._toast("Running genuine local inference..."); result = await asyncio.to_thread(LocalModelProvider(config_from_env()).plan, "Return a concise JSON TaskSpec for creating a deterministic local text utility.")
        self._toast(f"Inference {result.status}: {result.duration_ms} ms, {result.generated_tokens} generated tokens" if result.status == "ready" else f"Inference failed: {result.error or result.status}")
    def _toast(self, message): self.page.snack_bar = ft.SnackBar(ft.Text(message, color=TEXT), bgcolor=ELEVATED); self.page.snack_bar.open = True; self.page.update()
