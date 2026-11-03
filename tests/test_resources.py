import json
import subprocess
import sys

import httpx
from typer.testing import CliRunner

from canvas_study import cli
from canvas_study.client import CanvasClient
from canvas_study.resources import fetch_assignment, fetch_questions

BASE = "https://oc.sjtu.edu.cn"


def mock_client(handler):
    return CanvasClient(BASE, {"token": "fake"}, transport=httpx.MockTransport(handler))


def test_assignment_get_preserves_raw_fields_without_binding(tmp_path, monkeypatch):
    monkeypatch.setenv("CANVAS_STUDY_HOME", str(tmp_path / "unused-config"))
    assignment = {
        "id": 42,
        "description": '<p data-extra="x">原始说明</p>',
        "due_at": None,
        "rubric": [{"id": "r1"}],
        "submission_types": ["online_upload"],
        "custom_field": 123,
    }
    seen = []

    def handler(request):
        seen.append(request.url.path)
        return httpx.Response(200, json=assignment)

    monkeypatch.setattr(cli, "client", lambda: mock_client(handler))
    result = CliRunner().invoke(cli.app, ["--json", "assignments", "get", "999", "42"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["data"] == assignment
    assert seen == ["/api/v1/courses/999/assignments/42"]
    assert not list(tmp_path.iterdir())


def test_assignment_fetch_separates_data_from_conversion():
    seen = []
    assignment = {
        "id": 42,
        "name": "HW",
        "description": '<a href="/courses/999/files/123">附件</a>',
        "quiz_id": 9,
    }

    def handler(request):
        seen.append(request.url.path)
        if request.url.path.endswith("/assignments/42"):
            return httpx.Response(200, json=assignment)
        if request.url.path.endswith("/files/123"):
            return httpx.Response(200, json={"id": 123, "filename": "HW.pdf"})
        raise AssertionError("题目和提交记录必须显式请求")

    with mock_client(handler) as c:
        result = fetch_assignment(c, "999", "42")
    assert result["assignment"] == assignment
    assert result["attachments"]["items"][0]["id"] == 123
    assert result["questions"] is None and result["submission"] is None
    assert len(seen) == 2


def test_raw_questions_keep_complex_answer_fields():
    question = {
        "id": 1,
        "question_type": "matching_question",
        "question_text": "配对",
        "answers": [{"id": 2, "match_left": "A", "match_right": "B", "weight": 100}],
    }

    def handler(request):
        if request.url.path.endswith("/submissions"):
            return httpx.Response(
                200, json={"quiz_submissions": [{"id": 3, "attempt": 1, "finished_at": "x"}]}
            )
        return httpx.Response(200, json=[question])

    with mock_client(handler) as c:
        result = fetch_questions(c, "999", "9", expected=1)
    assert result["items"] == [question]
    assert not result["partial"]


def test_list_paginates_and_saves_raw_json(tmp_path, monkeypatch):
    rows = [{"id": 1, "name": "作业一", "extra": True}, {"id": 2, "name": "作业二", "extra": False}]

    def handler(request):
        if request.url.params.get("page") == "2":
            return httpx.Response(200, json=rows[1:])
        return httpx.Response(
            200,
            json=rows[:1],
            headers={"Link": f'<{BASE}/api/v1/courses/999/assignments?page=2>; rel="next"'},
        )

    monkeypatch.setattr(cli, "client", lambda: mock_client(handler))
    output = tmp_path / "assignments.json"
    result = CliRunner().invoke(cli.app, ["--json", "assignments", "list", "999", "--output", str(output)])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["data"] == rows
    assert json.loads(output.read_text("utf-8")) == rows
    assert not list(tmp_path.rglob("*.md"))


def test_default_sync_does_not_fetch_questions_or_export(tmp_path, monkeypatch):
    requested = []

    def handler(request):
        requested.append(request.url.path)
        if request.url.path == "/api/v1/courses/999":
            return httpx.Response(200, json={"id": 999, "name": "Another course"})
        if request.url.path.endswith("/assignments"):
            return httpx.Response(200, json=[{"id": 1, "name": "Quiz", "quiz_id": 9}])
        if request.url.path.endswith("/quizzes"):
            return httpx.Response(200, json=[{"id": 9, "title": "Quiz", "question_count": 5}])
        return httpx.Response(200, json=[])

    monkeypatch.setattr(cli, "client", lambda: mock_client(handler))
    monkeypatch.setenv("CANVAS_STUDY_HOME", str(tmp_path / "config"))
    result = CliRunner().invoke(cli.app, ["--json", "sync", "999", "--output", str(tmp_path / "course")])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["data"]["review"] is None
    assert not any(path.endswith(("/questions", "/submissions")) for path in requested)
    assert not list(tmp_path.rglob("*.md"))


def test_readonly_resource_endpoints(monkeypatch):
    seen = []

    def handler(request):
        assert request.method == "GET"
        seen.append(request.url.path)
        return httpx.Response(200, json=[] if request.url.path.endswith("/items") else {"id": 1})

    monkeypatch.setattr(cli, "client", lambda: mock_client(handler))
    runner = CliRunner()
    for args in (
        ["assignments", "submission", "999", "42"],
        ["modules", "items", "999", "3"],
        ["files", "get", "123"],
        ["pages", "get", "999", "lecture-1"],
    ):
        result = runner.invoke(cli.app, ["--json", *args])
        assert result.exit_code == 0, result.output
    assert seen == [
        "/api/v1/courses/999/assignments/42/submissions/self",
        "/api/v1/courses/999/modules/3/items",
        "/api/v1/files/123",
        "/api/v1/courses/999/pages/lecture-1",
    ]


def test_core_cli_imports_without_export_dependencies():
    code = "import sys; sys.modules['pypdf']=None; sys.modules['markdownify']=None; from canvas_study.cli import app; print('ok')"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_question_api_401_with_valid_session_uses_visible_page():
    from canvas_study.client import CanvasError
    from canvas_study.resources import fetch_questions
    import httpx

    class Client:
        auth = {"cookies": [{"name": "session", "value": "fake"}]}

        def quiz_questions(self, *args):
            raise CanvasError("Unauthorized", 401)

        def get(self, path):
            assert path == "/api/v1/users/self/profile"
            return {"id": 123}

        def request(self, url):
            return httpx.Response(
                200, text='<div class="question" id="q1"><div class="question_text">Question</div></div>'
            )

    result = fetch_questions(Client(), "1", "2", html_url="https://canvas.test/quizzes/2", expected=1)
    assert result["source"] == "result_html" and not result["partial"]


def test_question_api_401_with_expired_session_stops():
    from canvas_study.client import CanvasError
    from canvas_study.resources import fetch_questions
    import pytest

    class Client:
        auth = {"cookies": [{}]}

        def quiz_questions(self, *args):
            raise CanvasError("Unauthorized", 401)

        def get(self, path):
            raise CanvasError("Session expired", 401)

        def request(self, url):
            raise AssertionError("Must not continue after expired session")

    with pytest.raises(CanvasError, match="Session expired"):
        fetch_questions(Client(), "1", "2", html_url="https://canvas.test/quizzes/2")
