from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from chimera.applications.connectors.browser_connector import (
    BrowserConnector,
    _BrowserRuntime,
)


class FakeProcess:
    pid = 4321

    def poll(self):
        return None

    def terminate(self):
        self.terminated = True


class FakeLocator:
    def __init__(self, count=1, visible=True, enabled=True, editable=True, field_type="text", value=""):
        self._count = count
        self._visible = visible
        self._enabled = enabled
        self._editable = editable
        self._type = field_type
        self._value = value
        self.clicked = 0
        self.pressed = []

    def count(self):
        return self._count

    def is_visible(self):
        return self._visible

    def is_enabled(self):
        return self._enabled

    def is_editable(self):
        return self._editable

    def evaluate(self, script):
        if "Array.from(el.files" in script:
            return getattr(self, "files", [])
        if "isContentEditable" in script:
            return self._value
        return {
            "tag": "input", "type": self._type, "role": "",
            "disabled": not self._enabled, "readOnly": False,
            "value": self._value, "isContentEditable": False,
        }

    def select_option(self, value, **_kwargs):
        self.selected = [value]
        return self.selected

    def set_input_files(self, path, **_kwargs):
        source = Path(path)
        self.files = [{
            "name": source.name,
            "size": source.stat().st_size,
            "type": "application/octet-stream",
        }]

    def click(self, **_kwargs):
        self.clicked += 1

    def fill(self, value, **_kwargs):
        self._value = value

    def input_value(self, **_kwargs):
        return self._value

    def press(self, key, **_kwargs):
        self.pressed.append(key)


class FakeResponse:
    status = 200


class FakePage:
    def __init__(self, url="https://example.test/"):
        self.url = url
        self._title = "Example"
        self.locators = {}
        self.body_text = "Untrusted page body"
        self.goto_calls = []
        self.screenshot_calls = []

    def title(self):
        return self._title

    def locator(self, selector):
        if selector == "body":
            return SimpleNamespace(inner_text=lambda **_kwargs: self.body_text)
        if selector.startswith("button,a,input"):
            return SimpleNamespace(evaluate_all=lambda _script: [
                {"tag": "button", "text": "Continue", "visible": True, "password": False}
            ])
        return self.locators.get(("css", selector), FakeLocator())

    def get_by_role(self, role, name, exact):
        assert exact is True
        return self.locators.get(("role", role, name), FakeLocator())

    def get_by_label(self, selector, exact):
        assert exact is True
        return self.locators.get(("label", selector), FakeLocator())

    def get_by_placeholder(self, selector, exact):
        assert exact is True
        return self.locators.get(("placeholder", selector), FakeLocator())

    def get_by_text(self, selector, exact):
        assert exact is True
        return self.locators.get(("text", selector), FakeLocator())

    def get_by_test_id(self, selector):
        return self.locators.get(("test_id", selector), FakeLocator())

    def goto(self, url, **_kwargs):
        self.goto_calls.append(url)
        self.url = url
        return FakeResponse()

    def screenshot(self, path, **_kwargs):
        self.screenshot_calls.append(path)
        payload = b"\x89PNG\r\n\x1a\nfake-image-payload"
        Path(path).write_bytes(payload)
        return payload


class FakeContext:
    def __init__(self, pages):
        self.pages = pages


class FakeBrowser:
    def is_connected(self):
        return True


def make_connector(tmp_path):
    connector = BrowserConnector(browser_paths={"edge": tmp_path / "msedge.exe"})
    page = FakePage()
    context = FakeContext([page])
    connector._runtimes["session-1"] = _BrowserRuntime(
        playwright=object(),
        browser=FakeBrowser(),
        context=context,
        process=FakeProcess(),
        pid=4321,
        process_create_time=123.0,
        executable=str(tmp_path / "msedge.exe"),
        port=9333,
        profile=tmp_path / ".chimera" / "browser" / "session" / "profile",
        workspace=tmp_path,
    )
    return connector, page


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "file:///C:/Windows/win.ini",
        "data:text/html,hello",
        "https://user:password@example.com/",
        "not-a-url",
        "ftp://example.com/",
    ],
)
def test_browser_navigation_accepts_only_plain_http_or_https_urls(url):
    with pytest.raises(ValueError):
        BrowserConnector.validate_url(url)


def test_browser_navigation_accepts_http_and_https():
    assert BrowserConnector.validate_url("https://example.test/path") == "https://example.test/path"
    assert BrowserConnector.validate_url("http://127.0.0.1:8000/") == "http://127.0.0.1:8000/"


@pytest.mark.parametrize(
    "relative",
    [
        "../escape.txt",
        "/absolute.txt",
        "C:/Windows/win.ini",
        "folder/../../escape.txt",
        "folder//empty.txt",
    ],
)
def test_browser_workspace_file_rejects_escape_paths(tmp_path, relative):
    with pytest.raises(ValueError):
        BrowserConnector._workspace_file(tmp_path.resolve(), relative)


def test_browser_workspace_file_rejects_overwrite_and_wrong_suffix(tmp_path):
    (tmp_path / "existing.png").write_bytes(b"old")
    with pytest.raises(FileExistsError):
        BrowserConnector._workspace_file(tmp_path.resolve(), "existing.png")
    with pytest.raises(ValueError):
        BrowserConnector._workspace_file(tmp_path.resolve(), "report.txt", suffix=".png")


def test_browser_role_selector_is_parsed_as_role_and_accessible_name():
    page = FakePage()
    locator = FakeLocator()
    page.locators[("role", "button", "Continue")] = locator
    observed = BrowserConnector._locator(page, "role", "button:Continue")
    assert observed is locator


def test_browser_role_selector_rejects_unapproved_roles():
    page = FakePage()
    with pytest.raises(ValueError):
        BrowserConnector._locator(page, "role", "application:Run arbitrary code")


def test_browser_inspect_page_returns_bounded_untrusted_page_content(tmp_path):
    connector, page = make_connector(tmp_path)
    with connector.execution_context("session-1", tmp_path):
        result = connector.execute_action(
            "inspect_page",
            {"page_index": 0, "expected_url": "https://example.test/"},
        )

    assert result["status"] == "completed"
    assert result["title"] == "Example"
    assert result["text"] == "Untrusted page body"
    assert result["untrusted_content"] is True
    assert result["interactive_elements"][0]["text"] == "Continue"


def test_browser_actions_reject_stale_tab_url_before_interaction(tmp_path):
    connector, page = make_connector(tmp_path)
    with connector.execution_context("session-1", tmp_path):
        with pytest.raises(ValueError, match="changed since inspection"):
            connector.execute_action(
                "click",
                {
                    "page_index": 0, "expected_url": "https://old.example/",
                    "selector_kind": "role", "selector": "button:Continue",
                },
                approved=True,
            )


def test_browser_click_requires_approval_and_unique_visible_target(tmp_path):
    connector, page = make_connector(tmp_path)
    target = FakeLocator()
    page.locators[("role", "button", "Continue")] = target
    arguments = {
        "page_index": 0, "expected_url": page.url,
        "selector_kind": "role", "selector": "button:Continue",
    }
    with connector.execution_context("session-1", tmp_path):
        pending = connector.execute_action("click", arguments, approved=False)
        assert pending["status"] == "awaiting_approval"
        assert target.clicked == 0
        completed = connector.execute_action("click", arguments, approved=True)

    assert completed["status"] == "completed"
    assert completed["accepted"] is True
    assert target.clicked == 1

    page.locators[("role", "button", "Continue")] = FakeLocator(count=2)
    with connector.execution_context("session-1", tmp_path):
        with pytest.raises(ValueError, match="exactly one"):
            connector.execute_action("click", arguments, approved=True)


def test_browser_fill_verifies_value_and_refuses_password_fields(tmp_path):
    connector, page = make_connector(tmp_path)
    field = FakeLocator(value="")
    page.locators[("label", "Name")] = field
    arguments = {
        "page_index": 0, "expected_url": page.url,
        "selector_kind": "label", "selector": "Name", "value": "Ada",
    }
    with connector.execution_context("session-1", tmp_path):
        result = connector.execute_action("fill", arguments, approved=True)
    assert result["verified"] is True
    assert field.input_value() == "Ada"

    password = FakeLocator(field_type="password")
    page.locators[("label", "Password")] = password
    arguments["selector"] = "Password"
    arguments["value"] = "secret"
    with connector.execution_context("session-1", tmp_path):
        with pytest.raises(PermissionError, match="password fields"):
            connector.execute_action("fill", arguments, approved=True)


def test_browser_screenshot_requires_approval_and_saves_verified_workspace_file(tmp_path):
    connector, page = make_connector(tmp_path)
    arguments = {
        "page_index": 0, "expected_url": page.url,
        "relative_path": "screenshots/page.png",
    }
    with connector.execution_context("session-1", tmp_path):
        pending = connector.execute_action("screenshot", arguments, approved=False)
        assert pending["status"] == "awaiting_approval"
        completed = connector.execute_action("screenshot", arguments, approved=True)

    target = tmp_path / "screenshots" / "page.png"
    assert completed["status"] == "completed"
    assert completed["bytes"] == len(b"\x89PNG\r\n\x1a\nfake-image-payload")
    assert target.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert completed["independent_verification"] is True


def test_browser_manifest_marks_reads_and_mutations_with_distinct_permissions():
    connector = BrowserConnector()
    actions = {action.action_id: action for action in connector.manifest.actions}
    assert actions["inspect_page"].permission == "browser.read"
    assert actions["inspect_page"].requires_approval is False
    assert actions["click"].permission == "browser.input"
    assert actions["click"].requires_approval is True
    assert actions["screenshot"].permission == "browser.screenshot"
    assert actions["screenshot"].requires_approval is True


def test_browser_context_is_scoped_per_conversation(tmp_path):
    connector, _page = make_connector(tmp_path)
    with connector.execution_context("session-1", tmp_path):
        assert connector._session() == ("session-1", tmp_path.resolve())
    with pytest.raises(PermissionError):
        connector._session()



def test_browser_launch_requires_exact_operator_approval(tmp_path):
    connector = BrowserConnector(browser_paths={"edge": tmp_path / "msedge.exe"})
    pending = connector.execute_action("launch", {"browser_name": "edge"}, approved=False)
    assert pending == {"status": "awaiting_approval", "permission": "browser.launch"}
    assert connector._runtimes == {}


def test_browser_profile_is_separate_for_each_conversation(tmp_path):
    first = BrowserConnector._profile_directory(tmp_path.resolve(), "session-one")
    second = BrowserConnector._profile_directory(tmp_path.resolve(), "session-two")
    assert first != second
    assert tmp_path.resolve() in first.parents
    assert tmp_path.resolve() in second.parents
    assert first.name == "profile" and second.name == "profile"


def test_browser_connector_is_registered_and_read_actions_are_read_only(tmp_path):
    from chimera.applications.dispatch import APPLICATION_READ_PERMISSIONS, application_tool_catalog
    from chimera.applications.registry import ApplicationRegistry
    from chimera.applications.connectors.browser_connector import BrowserConnector

    registry = ApplicationRegistry()
    connector = BrowserConnector(browser_paths={})
    connector.manifest.is_installed = True
    registry.register(connector)
    actions = {item["id"]: item for item in application_tool_catalog(registry)}

    assert "browser.read" in APPLICATION_READ_PERMISSIONS
    assert "application.browser.inspect_page" in actions
    assert actions["application.browser.inspect_page"]["permission"] == "browser.read"
    assert actions["application.browser.click"]["permission"] == "browser.input"
    assert actions["application.browser.navigate"]["permission"] == "browser.navigate"
    assert actions["application.browser.screenshot"]["requires_approval"] is True
    assert actions["application.browser.upload_file"]["permission"] == "browser.upload"
    assert actions["application.browser.upload_file"]["requires_approval"] is True



def test_browser_playwright_actions_are_serialized_on_one_owned_thread(tmp_path):
    import threading

    connector, page = make_connector(tmp_path)
    observed_threads = []
    original_title = page.title

    def title():
        observed_threads.append(threading.get_ident())
        return original_title()

    page.title = title
    errors = []

    def run_action():
        try:
            with connector.execution_context("session-1", tmp_path):
                result = connector.execute_action("list_tabs", {})
                assert result["status"] == "completed"
        except Exception as exc:
            errors.append(exc)

    workers = [threading.Thread(target=run_action) for _ in range(4)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=5)

    assert not errors
    assert all(not worker.is_alive() for worker in workers)
    assert len(observed_threads) == 4
    assert set(observed_threads) == {connector._browser_worker_thread.ident}


def test_browser_discovery_does_not_mark_missing_executable_as_installed(tmp_path):
    missing = tmp_path / "missing-msedge.exe"
    connector = BrowserConnector(browser_paths={"edge": missing})
    connector.discover()

    assert connector.manifest.is_installed is False
    assert connector.manifest.installation_path is None
    assert connector._browser_paths == {}



def test_browser_select_option_requires_approval_and_verifies_selected_value(tmp_path):
    connector, page = make_connector(tmp_path)
    dropdown = FakeLocator()
    dropdown.evaluate = lambda _script: {
        "tag": "select", "type": "", "role": "",
        "disabled": False, "readOnly": False,
    }
    page.locators[("label", "Country")] = dropdown
    arguments = {
        "page_index": 0, "expected_url": page.url,
        "selector_kind": "label", "selector": "Country",
        "value": "IN",
    }
    with connector.execution_context("session-1", tmp_path):
        pending = connector.execute_action("select_option", arguments, approved=False)
        assert pending["status"] == "awaiting_approval"
        assert not hasattr(dropdown, "selected")
        completed = connector.execute_action("select_option", arguments, approved=True)

    assert completed["status"] == "completed"
    assert completed["verified"] is True
    assert dropdown.selected == ["IN"]


def test_browser_fill_rejects_file_and_checkbox_controls(tmp_path):
    connector, page = make_connector(tmp_path)
    for field_type in ("file", "checkbox", "radio", "hidden"):
        field = FakeLocator(field_type=field_type)
        page.locators[("label", field_type)] = field
        arguments = {
            "page_index": 0, "expected_url": page.url,
            "selector_kind": "label", "selector": field_type,
            "value": "not-an-upload",
        }
        with connector.execution_context("session-1", tmp_path):
            with pytest.raises(ValueError, match="plain-text"):
                connector.execute_action("fill", arguments, approved=True)


def test_browser_manifest_exposes_select_option_as_approved_input():
    connector = BrowserConnector()
    action = next(item for item in connector.manifest.actions if item.action_id == "select_option")
    assert action.permission == "browser.input"
    assert action.requires_approval is True



def test_browser_upload_requires_approval_and_verifies_workspace_file(tmp_path):
    connector, page = make_connector(tmp_path)
    source = tmp_path / "attachments" / "notes.txt"
    source.parent.mkdir()
    source.write_text("meeting notes", encoding="utf-8")
    file_input = FakeLocator()
    file_input.evaluate = lambda script: (
        [{"name": "notes.txt", "size": source.stat().st_size, "type": "text/plain"}]
        if "Array.from(el.files" in script else
        {"tag": "input", "type": "file", "disabled": False, "multiple": False, "accept": ".txt"}
    )
    page.locators[("label", "Attachment")] = file_input
    arguments = {
        "page_index": 0, "expected_url": page.url,
        "selector_kind": "label", "selector": "Attachment",
        "relative_path": "attachments/notes.txt",
    }
    with connector.execution_context("session-1", tmp_path):
        pending = connector.execute_action("upload_file", arguments, approved=False)
        assert pending["status"] == "awaiting_approval"
        completed = connector.execute_action("upload_file", arguments, approved=True)

    assert completed["status"] == "completed"
    assert completed["verified"] is True
    assert completed["uploaded_file"]["path"] == "attachments/notes.txt"
    assert completed["uploaded_file"]["bytes"] == len(b"meeting notes")


def test_browser_upload_rejects_non_file_inputs_and_workspace_escape(tmp_path):
    connector, page = make_connector(tmp_path)
    ordinary = FakeLocator()
    page.locators[("label", "Ordinary")] = ordinary
    arguments = {
        "page_index": 0, "expected_url": page.url,
        "selector_kind": "label", "selector": "Ordinary",
        "relative_path": "notes.txt",
    }
    (tmp_path / "notes.txt").write_text("hello", encoding="utf-8")
    with connector.execution_context("session-1", tmp_path):
        with pytest.raises(ValueError, match="input\\[type=file\\]"):
            connector.execute_action("upload_file", arguments, approved=True)
        file_input = FakeLocator()
        file_input.evaluate = lambda _script: {
            "tag": "input", "type": "file", "disabled": False,
            "multiple": False, "accept": "",
        }
        page.locators[("label", "File")] = file_input
        arguments["selector"] = "File"
        arguments["relative_path"] = "../outside.txt"
        with pytest.raises(ValueError, match="inside the selected workspace"):
            connector.execute_action("upload_file", arguments, approved=True)


def test_browser_upload_manifest_is_sensitive_and_requires_approval():
    connector = BrowserConnector()
    action = next(item for item in connector.manifest.actions if item.action_id == "upload_file")
    assert action.permission == "browser.upload"
    assert action.requires_approval is True
    assert action.risk_level.value == 4
