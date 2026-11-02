import json
import ast
from pathlib import Path

from scripts.gui_acceptance import (AcceptanceRun, DEFAULT_PROMPT, atomic_write,
                                    create_manual_prompt_checkpoint, owned_processes,
                                    report, verify_prompt_text)


def test_acceptance_run_persists_manifest_and_append_only_events(tmp_path):
    run = AcceptanceRun.create(tmp_path, "local-readonly-inspection", DEFAULT_PROMPT, tmp_path / "workspace")
    run.checkpoint("workspace_selection", 60)
    run.event("observation", {"status": "selected"})
    manifest = json.loads((run.run_dir / "run.json").read_text(encoding="utf-8"))
    events = (run.run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert manifest["prompt_source"] == "acceptance_harness"
    assert manifest["prompt"] == DEFAULT_PROMPT
    assert manifest["last_checkpoint"] == "workspace_selection"
    assert [json.loads(line)["event"] for line in events] == ["run.created", "checkpoint", "observation"]


def test_acceptance_run_ids_are_unique_and_submission_starts_zero(tmp_path):
    first = AcceptanceRun.create(tmp_path, "scenario", DEFAULT_PROMPT)
    second = AcceptanceRun.create(tmp_path, "scenario", DEFAULT_PROMPT)
    assert first.manifest["run_id"] != second.manifest["run_id"]
    assert first.manifest["submission_count"] == 0
    assert second.manifest["submission_count"] == 0


def test_report_does_not_infer_model_or_tool_success(tmp_path):
    run = AcceptanceRun.create(tmp_path, "scenario", DEFAULT_PROMPT)
    text = report(run)
    assert "no model decision or tool invocation is inferred" in text
    assert (run.run_dir / "report.md").is_file()


def test_acceptance_harness_has_no_global_input_apis():
    scripts_dir = Path(__file__).parents[1].joinpath("scripts")
    prohibited = {"type_keys", "click_input", "send_keys", "SendInput", "keybd_event", "mouse_event"}
    violations = []
    for path in scripts_dir.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in prohibited:
                violations.append(f"{path.name}:{node.attr}")
            if isinstance(node, ast.Name) and node.id in {"pyautogui", "pynput", "keyboard", "mouse"}:
                violations.append(f"{path.name}:{node.id}")
    assert not violations, f"global desktop input API present: {violations}"


def test_acceptance_manifest_records_safe_interaction_policy(tmp_path):
    run = AcceptanceRun.create(tmp_path, "safe", "prompt")
    safety = run.manifest["interaction_safety"]
    assert safety["global_keyboard_injection_used"] is False
    assert safety["global_mouse_injection_used"] is False
    assert safety["clipboard_modified"] is False


def test_manual_prompt_checkpoint_requires_operator_and_verifies_exact_text(tmp_path):
    run = AcceptanceRun.create(tmp_path, "safe", DEFAULT_PROMPT)
    create_manual_prompt_checkpoint(run)
    assert run.manifest["stage"] == "awaiting_manual_prompt_entry"
    assert run.manifest["state"] == "awaiting_operator"
    assert verify_prompt_text(run, DEFAULT_PROMPT) is True
    assert verify_prompt_text(run, DEFAULT_PROMPT + " altered") is False
    assert run.manifest["submission_count"] == 0


def test_owned_process_tree_includes_children_but_not_unrelated_processes():
    processes = [
        {"pid": 10, "parent_pid": 1},
        {"pid": 11, "parent_pid": 10},
        {"pid": 12, "parent_pid": 11},
        {"pid": 99, "parent_pid": 1},
    ]
    assert [item["pid"] for item in owned_processes(processes, 10)] == [10, 11, 12]
