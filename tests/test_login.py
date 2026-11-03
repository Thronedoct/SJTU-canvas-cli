import os
from unittest.mock import MagicMock

from typer.testing import CliRunner

from canvas_study import auth, cli
from canvas_study.client import CanvasClient, normalize_no_proxy


def test_ipv6_no_proxy_does_not_break_client_or_change_environment(monkeypatch):
    bypass = "localhost,127.0.0.1,[::1],[::1]:8000,.example.test"
    monkeypatch.setenv("no_proxy", bypass)
    assert normalize_no_proxy(bypass) == "localhost,127.0.0.1,all://[::1],all://[::1]:8000,.example.test"
    with CanvasClient("https://oc.sjtu.edu.cn", {}):
        assert os.environ["no_proxy"] == bypass
    assert os.environ["no_proxy"] == bypass


def test_browser_auth_is_saved_before_window_closes(monkeypatch):
    import playwright.sync_api

    manager = MagicMock()
    browser = manager.__enter__.return_value.chromium.launch.return_value
    context = browser.new_context.return_value
    response = context.request.get.return_value
    response.ok = True
    response.headers = {"content-type": "application/json"}
    response.json.return_value = {"id": 1, "name": "Test user"}
    context.cookies.return_value = [{"domain": "oc.sjtu.edu.cn", "name": "session", "value": "fake"}]
    events = []
    monkeypatch.setattr(playwright.sync_api, "sync_playwright", lambda: manager)
    monkeypatch.setattr("builtins.input", lambda: "")
    monkeypatch.setattr(auth, "save_auth", lambda value: events.append("saved"))
    browser.close.side_effect = lambda: events.append("closed")
    credential, profile = auth.browser_login("https://oc.sjtu.edu.cn")
    assert credential["cookies"][0]["name"] == "session"
    assert profile["id"] == 1
    assert events == ["saved", "closed"]


def test_browser_login_does_not_repeat_httpx_validation(monkeypatch):
    monkeypatch.setattr(cli, "browser_login", lambda *a: ({"cookies": []}, {"id": 1, "name": "Test user"}))

    def fail(*args, **kwargs):
        raise AssertionError("browser API has already validated login")

    monkeypatch.setattr(cli, "CanvasClient", fail)
    result = CliRunner().invoke(cli.app, ["--json", "login", "--browser"])
    assert result.exit_code == 0, result.output
    assert '"authenticated": true' in result.stdout
