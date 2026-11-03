import json
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from canvas_study import cli
from canvas_study.auth import load_auth, save_auth
from canvas_study.catalog import clean_html, discover, file_ids
from canvas_study.client import CanvasClient, CanvasError
from canvas_study.download import destination, download_one, fingerprint, sync_files
from canvas_study.review import generate_review, markdown, questions_from_html
from canvas_study.storage import CourseLock, safe_name, sha256, under, write_json

BASE = "https://oc.sjtu.edu.cn"


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("CANVAS_STUDY_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("CANVAS_TOKEN", raising=False)
    return tmp_path


def client(handler, **kwargs):
    return CanvasClient(
        BASE, {"token": "test-secret"}, transport=httpx.MockTransport(handler), sleep=lambda _: None, **kwargs
    )


def file_record(**kwargs):
    return {
        "id": "12",
        "name": "电路课件.pdf",
        "size": 6,
        "updated_at": "2026-11-03T00:00:00Z",
        "sources": ["01_反相器"],
        "locked": False,
        **kwargs,
    }


def test_pagination_and_token_scope():
    seen = []

    def handler(req):
        seen.append(req)
        if req.url.params.get("page") == "2":
            return httpx.Response(200, json=[{"id": 2}])
        return httpx.Response(
            200, json=[{"id": 1}], headers={"Link": f'<{BASE}/api/v1/courses?page=2>; rel="next"'}
        )

    with client(handler) as c:
        assert [x["id"] for x in c.courses()] == [1, 2]
    assert all(r.headers["Authorization"] == "Bearer test-secret" for r in seen)


def test_external_pagination_rejected():
    with client(
        lambda _: httpx.Response(200, json=[], headers={"Link": '<https://evil.test/steal>; rel="next"'})
    ) as c:
        with pytest.raises(CanvasError, match="其他站点"):
            c.courses()


def test_download_redirect_never_leaks_credentials():
    seen = []

    def handler(req):
        seen.append(req)
        if req.url.host == "oc.sjtu.edu.cn":
            return httpx.Response(302, headers={"Location": "https://cdn.test/file"})
        return httpx.Response(200, content=b"data")

    with client(handler) as c:
        c.auth["cookies"] = [{"name": "session", "value": "test-cookie", "path": "/"}]
        c.request(BASE + "/files/12/download", api=False).close()
    assert "Authorization" in seen[0].headers and "Cookie" in seen[0].headers
    assert "Authorization" not in seen[1].headers and "Cookie" not in seen[1].headers


def test_retry_and_login_detection():
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(429 if len(calls) < 3 else 200, json={"id": 1})

    with client(handler) as c:
        assert c.get("/api/v1/users/self")["id"] == 1
    assert len(calls) == 3
    with client(lambda _: httpx.Response(302, headers={"Location": "/login"})) as c:
        with pytest.raises(CanvasError, match="登录"):
            c.get("/api/v1/courses")


@pytest.mark.parametrize("name", ["../恶意.pdf", "CON", "NUL.txt", "a:b?c.pptx", "..", "a" * 200 + ".pdf"])
def test_safe_windows_names(name):
    name = safe_name(name)
    assert len(name) <= 100
    assert not any(ch in name for ch in '/\\:*?"<>|')
    assert name not in {"CON", "NUL.txt", ".."}


def test_manifest_path_cannot_escape(tmp_path):
    with pytest.raises(ValueError):
        under(tmp_path, "../outside")


def test_resume_range_and_digest(tmp_path):
    file = file_record()
    target = tmp_path / "sample.pdf"
    target.with_name(target.name + ".part").write_bytes(b"abc")
    write_json(
        target.with_name(target.name + ".part.json"), {"fingerprint": fingerprint(file), "validator": '"v1"'}
    )

    def handler(req):
        if "/api/" in req.url.path:
            return httpx.Response(200, json={**file, "url": BASE + "/files/12/download"})
        assert req.headers["Range"] == "bytes=3-"
        assert req.headers["If-Range"] == '"v1"'
        return httpx.Response(206, content=b"def", headers={"Content-Range": "bytes 3-5/6", "ETag": '"v1"'})

    with client(handler) as c:
        result = download_one(c, file, target)
    assert target.read_bytes() == b"abcdef"
    assert result["sha256"] == sha256(target)


def test_server_ignoring_range_restarts(tmp_path):
    file = file_record()
    target = tmp_path / "sample.pdf"
    target.with_name(target.name + ".part").write_bytes(b"BAD")
    write_json(
        target.with_name(target.name + ".part.json"), {"fingerprint": fingerprint(file), "validator": '"v1"'}
    )

    def handler(req):
        if "/api/" in req.url.path:
            return httpx.Response(200, json={**file, "url": BASE + "/file"})
        return httpx.Response(200, content=b"abcdef")

    with client(handler) as c:
        download_one(c, file, target)
    assert target.read_bytes() == b"abcdef"


@pytest.mark.parametrize("kind", ["size", "html", "range"])
def test_invalid_download_never_becomes_completed(tmp_path, kind):
    file = file_record()
    target = tmp_path / "sample.pdf"

    def handler(req):
        if "/api/" in req.url.path:
            return httpx.Response(200, json={**file, "url": BASE + "/file"})
        if kind == "html":
            return httpx.Response(200, content=b"login!", headers={"Content-Type": "text/html"})
        if kind == "range":
            return httpx.Response(206, content=b"abcdef", headers={"Content-Range": "bytes 2-7/8"})
        return httpx.Response(200, content=b"short")

    with client(handler) as c:
        with pytest.raises(CanvasError):
            download_one(c, file, target)
    assert not target.exists()


def test_dry_run_and_tamper_detection(tmp_path):
    file = file_record()
    catalog = {"files": [file]}
    folder = tmp_path / "new"
    with client(lambda _: pytest.fail("dry-run must not download")) as c:
        assert sync_files(c, catalog, folder, dry_run=True)[0]["status"] == "pending"
    assert not folder.exists()
    target = under(folder, destination(file))
    target.parent.mkdir(parents=True)
    target.write_bytes(b"abcdef")
    write_json(
        folder / ".canvas/manifest.json",
        {
            "files": {
                "12": {
                    "fingerprint": fingerprint(file),
                    "path": destination(file),
                    "size": 6,
                    "sha256": sha256(target),
                }
            }
        },
    )
    with client(lambda _: pytest.fail("verified files must not download")) as c:
        assert sync_files(c, catalog, folder)[0]["status"] == "unchanged"
        target.write_bytes(b"edited")
        assert sync_files(c, catalog, folder)[0]["status"] == "failed"
    assert target.read_bytes() == b"edited"


def test_equations_images_and_html_import():
    html = '<p>求 <img class="equation_image" alt="V_{DD}" src="/equation.png">：</p>'
    html += '<script type="math/tex">t_p=RC</script><script>alert(1)</script>'
    html += '<img src="/courses/1/files/12/preview">'
    md = markdown(clean_html(html, BASE), {"12": "../课件/diagram.png"})
    assert "$V_{DD}$" in md and "$t_p=RC$" in md
    assert "alert(1)" not in md and "/courses/1/files/12" not in md
    assert file_ids(html, BASE) == ["12"]
    assert file_ids('<a href="https://evil.test/files/9">bad</a>', BASE) == []
    rows = questions_from_html(
        '<div class="question"><div class="question_text">题干</div>'
        '<div class="answer"><div class="answer_text">选项 A</div></div></div>'
    )
    assert len(rows) == 1 and "选项 A" in rows[0]["answers"][0]


def test_quiz_uses_completed_attempt_and_no_posts():
    paths = []

    def handler(req):
        assert req.method == "GET"
        paths.append(req.url.path)
        if req.url.path.endswith("/submissions"):
            return httpx.Response(
                200,
                json={
                    "quiz_submissions": [
                        {"id": 4, "attempt": 1, "finished_at": "2026-01-01"},
                        {"id": 5, "attempt": 2, "finished_at": "2026-01-02"},
                    ]
                },
            )
        assert req.url.params["quiz_submission_id"] == "5"
        assert req.url.params["quiz_submission_attempt"] == "2"
        return httpx.Response(200, json=[{"id": 8, "question_text": "Q"}])

    with client(handler) as c:
        assert c.quiz_questions("1", "2")[0]["id"] == 8
    assert len(paths) == 2


def test_discovery_download_review_integration(tmp_path):
    file = file_record(name="notes.txt")

    def handler(req):
        path = req.url.path
        responses = {
            "/api/v1/courses/1": {"id": 1, "name": "演示课程（测试数据）"},
            "/api/v1/courses/1/files": [{**file, "display_name": file["name"]}],
            "/api/v1/courses/1/modules": [{"id": 3, "name": "反相器"}],
            "/api/v1/courses/1/modules/3/items": [{"type": "File", "content_id": 12}],
            "/api/v1/courses/1/assignments": [
                {
                    "id": 9,
                    "name": "测试测验",
                    "quiz_id": 2,
                    "html_url": BASE + "/courses/1/assignments/9",
                    "description": "<p>练习题</p>",
                }
            ],
            "/api/v1/courses/1/quizzes": [{"id": 2, "title": "测试测验", "question_count": 1}],
            "/api/v1/courses/1/quizzes/2/submissions": {
                "quiz_submissions": [{"id": 5, "attempt": 1, "finished_at": "2026-01-01"}]
            },
            "/api/v1/courses/1/quizzes/2/questions": [
                {"id": 8, "question_text": "求 RC 延时", "answers": [{"text": "0.69RC"}, {"text": "2.2RC"}]}
            ],
            "/api/v1/files/12": {**file, "url": BASE + "/download"},
        }
        if path == "/download":
            return httpx.Response(200, content=b"abcdef")
        return httpx.Response(200, json=responses[path])

    with client(handler) as c:
        catalog = discover(c, "1", questions=True)
        first = sync_files(c, catalog, tmp_path)
        second = sync_files(c, catalog, tmp_path)
    assert first[0]["status"] == "downloaded"
    assert second[0]["status"] == "unchanged"
    result = generate_review(catalog, tmp_path)
    text = Path(result["path"]).read_text(encoding="utf-8")
    assert "求 RC 延时" in text and "0.69RC" in text
    assert result["questions"] == 1 and not result["issues"]
    assert "test-secret" not in json.dumps(catalog)


def test_partial_review_is_explicit(tmp_path):
    catalog = {
        "course_id": "1",
        "course_name": "测试",
        "base_url": BASE,
        "fetched_at": "2026-01-01",
        "files": [],
        "warnings": ["文件库拒绝访问"],
        "assignments": [
            {
                "id": "1",
                "title": "不可见测验",
                "url": BASE,
                "quiz_id": "2",
                "questions": [],
                "expected_questions": 20,
                "attachments": ["12"],
            }
        ],
    }
    result = generate_review(catalog, tmp_path)
    assert len(result["issues"]) == 3
    assert "题目未完整取得" in Path(result["path"]).read_text(encoding="utf-8")


def test_course_lock(tmp_path):
    with CourseLock(tmp_path):
        with pytest.raises(ValueError, match="正在同步"):
            with CourseLock(tmp_path):
                pass


def test_cli_bind_doctor_json_and_missing_login(isolated):
    runner = CliRunner()
    target = isolated / "数集"
    assert runner.invoke(cli.app, ["bind", "93742", str(target), "--name", "数集"]).exit_code == 0
    assert not target.exists()
    result = runner.invoke(cli.app, ["--json", "doctor"])
    assert json.loads(result.stdout)["data"]["bound_courses"] == 1
    result = runner.invoke(cli.app, ["--json", "courses"])
    assert result.exit_code == 2
    assert json.loads(result.stdout)["ok"] is False


@pytest.mark.skipif(__import__("os").name != "nt", reason="Windows DPAPI")
def test_dpapi_round_trip_without_plaintext(isolated):
    save_auth({"base_url": BASE, "token": "fake-unit-test-secret"})
    assert load_auth(BASE)["token"] == "fake-unit-test-secret"
    assert b"fake-unit-test-secret" not in (isolated / "config/auth.dpapi").read_bytes()
    with pytest.raises(ValueError, match="不匹配"):
        load_auth("https://another.test")


def test_html_fallback_after_quiz_api_denied():
    class FakeClient:
        base = BASE
        auth = {"cookies": [{"name": "session", "value": "fake"}]}

        def get(self, path):
            return {"name": "测试课程"}

        def list(self, path, params=None):
            if path.endswith("/assignments"):
                return [
                    {"id": 1, "name": "测验", "quiz_id": 2, "html_url": BASE + "/courses/1/assignments/1"}
                ]
            if path.endswith("/quizzes"):
                return [{"id": 2, "title": "测验", "question_count": 1}]
            return []

        def quiz_questions(self, *args):
            raise CanvasError("Forbidden", 403)

        def request(self, path):
            return httpx.Response(
                200, text='<div class="question"><div class="question_text">HTML fallback</div></div>'
            )

    result = discover(FakeClient(), "1", questions=True)
    assert result["assignments"][0]["question_status"] == "available_html"
    assert "HTML fallback" in result["assignments"][0]["questions"][0]["body"]


def test_unfinished_quiz_never_started():
    with client(lambda req: httpx.Response(200, json={"quiz_submissions": []})) as c:
        with pytest.raises(CanvasError, match="不会自动开始"):
            c.quiz_questions("1", "2")


def test_cli_demo_review_reports_partial(isolated):
    catalog = json.loads((Path(__file__).parent.parent / "examples/catalog.demo.json").read_text("utf-8"))
    directory = isolated / "演示"
    write_json(directory / ".canvas/catalog.json", catalog)
    result = CliRunner().invoke(cli.app, ["--json", "review", "100", "--output", str(directory)])
    assert result.exit_code == 3
    payload = json.loads(result.stdout)
    assert payload["data"]["questions"] == 1
    assert "$\\tau=RC$" in Path(payload["data"]["path"]).read_text("utf-8")


def test_cli_version():
    result = CliRunner().invoke(cli.app, ["--version"])
    assert result.exit_code == 0 and "0.3.0" in result.stdout


def test_plain_names_and_reorganized_paths_persist(tmp_path):
    first = file_record()
    second = file_record(id="13", name="new.pdf")
    write_json(
        tmp_path / ".canvas/manifest.json",
        {
            "files": {},
            "filename_policy": "plain",
            "path_overrides": {"12": "课件/主读/01_MOS.pdf"},
        },
    )
    catalog = {"files": [first, second]}

    def handler(req):
        if "/api/" in req.url.path:
            item = first if req.url.path.endswith("/12") else second
            return httpx.Response(200, json={**item, "url": BASE + "/download/" + item["id"]})
        return httpx.Response(200, content=b"abcdef")

    with client(handler) as c:
        planned = sync_files(c, catalog, tmp_path, dry_run=True)
        assert planned[0]["path"] == "课件/主读/01_MOS.pdf"
        assert planned[1]["path"] == "课件/01_反相器/new.pdf"
        assert all(x["status"] == "downloaded" for x in sync_files(c, catalog, tmp_path))
        assert all(x["status"] == "unchanged" for x in sync_files(c, catalog, tmp_path))
        (tmp_path / planned[0]["path"]).write_bytes(b"my edit")
        assert sync_files(c, catalog, tmp_path)[0]["status"] == "failed"
        assert (tmp_path / planned[0]["path"]).read_bytes() == b"my edit"


def test_custom_download_path_stays_inside_root(tmp_path):
    write_json(
        tmp_path / ".canvas/manifest.json",
        {
            "files": {},
            "path_overrides": {"12": "../outside.pdf"},
        },
    )
    with pytest.raises(ValueError):
        sync_files(None, {"files": [file_record()]}, tmp_path, dry_run=True)
