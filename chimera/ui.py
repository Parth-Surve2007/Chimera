from pathlib import Path
from .review import ReviewService

try:
    from PySide6.QtWidgets import QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QListWidget, QStackedWidget, QLabel, QPushButton, QTableWidget, QTableWidgetItem, QPlainTextEdit, QMessageBox, QInputDialog, QSplitter
    from PySide6.QtCore import Qt
except ImportError: QApplication = None

def launch(registry):
    if QApplication is None: raise RuntimeError("PySide6 is not installed; run python -m pip install -e .")
    app = QApplication.instance() or QApplication([]); window = MainWindow(registry); window.show(); return app.exec()

class MainWindow(QMainWindow):
    def __init__(self, registry):
        super().__init__(); self.registry = registry; self.review = ReviewService(registry.data_dir); self.setWindowTitle("Chimera"); self.resize(1100, 700)
        root = QWidget(); layout = QHBoxLayout(root); self.nav = QListWidget(); self.nav.addItems(["Overview", "Capabilities", "Runs", "Settings"]); self.nav.setFixedWidth(180)
        self.pages = QStackedWidget(); self.pages.addWidget(self.overview()); self.pages.addWidget(self.capabilities()); self.pages.addWidget(self.placeholder("Runs\nNo executions recorded yet.")); self.pages.addWidget(self.placeholder("Settings\nLocal-first mode. No model provider configured.")); self.nav.currentRowChanged.connect(self.pages.setCurrentIndex); layout.addWidget(self.nav); layout.addWidget(self.pages); self.setCentralWidget(root)
    def overview(self):
        w = QWidget(); l = QVBoxLayout(w); l.addWidget(QLabel("CHIMERA\nLocal-first capability engine")); l.addWidget(QLabel("Task lifecycle")); self.task = QPlainTextEdit(); self.task.setPlaceholderText("Describe a task for Chimera..."); l.addWidget(self.task); l.addWidget(QPushButton("Submit task", clicked=lambda: QMessageBox.information(self, "Task", "Local model or workflow is not configured for this action."))); return w
    def capabilities(self):
        w = QWidget(); outer = QVBoxLayout(w); outer.addWidget(QLabel("Capabilities and candidate review")); split = QSplitter(Qt.Horizontal); self.candidate_table = QTableWidget(0, 3); self.candidate_table.setObjectName("candidateTable"); self.candidate_table.setHorizontalHeaderLabels(["ID", "Name", "State"]); self.candidate_table.itemSelectionChanged.connect(self.show_candidate); split.addWidget(self.candidate_table)
        right = QWidget(); self.detail_layout = QVBoxLayout(right); self.details = QLabel("Select a candidate. Static verification is not behavioral verification."); self.details.setWordWrap(True); self.detail_layout.addWidget(self.details); self.actions = QHBoxLayout(); self.verify_btn = QPushButton("Verify", clicked=self.verify_candidate); self.approve_btn = QPushButton("Approve acceptance", clicked=self.approve_acceptance); self.accept_btn = QPushButton("Accept", clicked=self.accept_candidate); self.reject_btn = QPushButton("Reject", clicked=self.reject_candidate)
        for button in (self.verify_btn, self.approve_btn, self.accept_btn, self.reject_btn): self.actions.addWidget(button)
        self.detail_layout.addLayout(self.actions); split.addWidget(right); outer.addWidget(split); self.refresh_btn = QPushButton("Refresh", clicked=self.refresh_candidates); outer.addWidget(self.refresh_btn); self.refresh_candidates(); return w
    def refresh_candidates(self):
        self.candidate_table.setRowCount(0)
        try: candidates = self.review.list()
        except Exception as exc: self.details.setText(f"Unable to load candidates: {exc}"); return
        for candidate in candidates:
            row = self.candidate_table.rowCount(); self.candidate_table.insertRow(row)
            for col, value in enumerate((candidate.candidate_id, candidate.name, candidate.status)): self.candidate_table.setItem(row, col, QTableWidgetItem(str(value)))
        self.details.setText("No candidates awaiting review." if not candidates else "Select a candidate. Static verification is not behavioral verification.")
    def selected_id(self):
        items = self.candidate_table.selectedItems(); return items[0].text() if items else None
    def show_candidate(self):
        try:
            c = self.review.get(self.selected_id()); self.details.setText(f"ID: {c.candidate_id}\nState: {c.status}\nTask: {c.task}\nManifest: {c.digest or 'not verified'}\nPermissions: {', '.join(c.permissions) or 'none'}\nExecution approval: {c.execution_approved}\nAcceptance approval: {c.acceptance_approved}\nStatic checks must not be described as behavioral verification.")
        except Exception as exc: self.details.setText(str(exc))
    def verify_candidate(self): self._action(lambda: self.review.verify(self.selected_id()), "Verification")
    def approve_acceptance(self): self._action(lambda: self.review.approve_acceptance(self.selected_id()), "Acceptance approval")
    def accept_candidate(self):
        cid = self.selected_id()
        if not cid: return
        if QMessageBox.question(self, "Confirm acceptance", f"Accept candidate {cid}? This does not execute its files.") != QMessageBox.Yes: return
        self._action(lambda: self.review.accept(cid), "Acceptance")
    def reject_candidate(self):
        cid = self.selected_id();
        if not cid: return
        reason, ok = QInputDialog.getText(self, "Reject candidate", "Reason (required):")
        if ok and reason.strip(): self._action(lambda: self.review.reject(cid, reason.strip()), "Rejection")
    def _action(self, action, title):
        try: action(); QMessageBox.information(self, title, "Completed through the shared review service."); self.refresh_candidates()
        except Exception as exc: QMessageBox.critical(self, title + " failed", str(exc))
    def placeholder(self, text): w = QWidget(); l = QVBoxLayout(w); l.addWidget(QLabel(text)); return w
