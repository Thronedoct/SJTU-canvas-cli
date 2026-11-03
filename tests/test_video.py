import json
from contextlib import nullcontext

import httpx
import pytest
from typer.testing import CliRunner

from canvas_study import cli, video_cli
from canvas_study.client import CanvasError
from canvas_study.transcripts import (
    normalize_transcript,
    render_transcript,
    search_transcript,
    select_recordings,
)
from canvas_study.video import (
    VIDEO_ORIGIN,
    VideoClient,
    api_data,
    checked_url,
    login_form,
    token_from_url,
)

BASE = "https://oc.sjtu.edu.cn"
RECORD = {"id": 7, "subjName": "测试课", "courBeginTime": "2026-10-01 08:00:00"}


def sample():
    return normalize_transcript(
        {
            "beforeAssemblyList": [
                {"bg": 1000, "ed": 2500, "res": "CMOS 反相器"},
                {"bg": 2000, "ed": 2000, "res": "传播延迟"},
            ],
            "afterAssemblyList": [{"bg": 1000, "ed": 4000, "res": "CMOS 反相器的传播延迟"}],
        },
        course_id="123",
        record=RECORD,
    )


class CanvasStub:
    base = BASE
    auth = {}

    def list(self, *args):
        return [{"id": 98765, "name": "课堂视频新版", "url": VIDEO_ORIGIN + "/lti/login"}]

    def get(self, path, params):
        assert params["id"] == "98765"
        return {"url": BASE + "/lti/start?one_time=fake"}

    def request(self, path):
        return httpx.Response(
            200,
            request=httpx.Request("GET", BASE + path),
            text='<form method="post" action="https://v.sjtu.edu.cn/lti/login">'
            '<input type="hidden" name="login_hint" value="fake"></form>',
        )


def test_course_lti_login_and_credential_scoping():
    canvas = CanvasStub()
    canvas.auth = {"cookies": [{"name": "canvas_session", "value": "fake-secret", "path": "/"}]}
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.host == "v.sjtu.edu.cn" and request.url.path == "/lti/login":
            assert request.method == "POST"
            assert "fake-secret" not in request.headers.get("Cookie", "")
            assert "Authorization" not in request.headers
            return httpx.Response(302, headers={"Location": BASE + "/api/lti/authorize"})
        if request.url.host == "oc.sjtu.edu.cn":
            assert "fake-secret" in request.headers.get("Cookie", "")
            return httpx.Response(302, headers={"Location": VIDEO_ORIGIN + "/ui/#/video?jwt_token=fake-jwt"})
        assert request.headers["jwt-token"] == "fake-jwt"
        assert "fake-secret" not in request.headers.get("Cookie", "")
        return httpx.Response(200, json={"ok": True, "data": {"canvasRecord": {"teachingClassId": 15}}})

    with VideoClient(canvas, "123", transport=httpx.MockTransport(handler)) as video:
        assert video.teaching_class() == "15"
    assert len(requests) == 3


def test_token_launch_flow():
    def handler(request):
        assert "Authorization" not in request.headers
        if request.url.host == "oc.sjtu.edu.cn":
            return httpx.Response(
                200,
                text='<form method="post" action="https://v.sjtu.edu.cn/lti/launch">'
                '<input name="id_token" type="hidden" value="fake-id-token"></form>',
            )
        assert request.method == "POST"
        return httpx.Response(302, headers={"Location": VIDEO_ORIGIN + "/?jwt_token=fake-video-token"})

    with VideoClient(CanvasStub(), "123", transport=httpx.MockTransport(handler)) as video:
        assert video.launch() == "fake-video-token"


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.test/lti",
        "http://v.sjtu.edu.cn/lti",
        "https://v.sjtu.edu.cn.evil.test/lti",
        "https://name:secret@v.sjtu.edu.cn/lti",
    ],
)
def test_unsafe_lti_targets_rejected(url):
    with pytest.raises(CanvasError):
        checked_url(url, BASE)
    html = f'<form method="post" action="{url}"><input name="id_token" value="secret"></form>'
    with pytest.raises(CanvasError):
        login_form(html, BASE, BASE)


def test_unrelated_form_not_submitted():
    with pytest.raises(CanvasError):
        login_form(
            '<form method="post" action="/courses/1/assignments"><input name="name"></form>', BASE, BASE
        )


def test_video_api_redirect_never_forwards_jwt():
    calls = []

    def handler(request):
        calls.append(request.url.host)
        return httpx.Response(302, headers={"Location": "https://evil.test/steal"})

    with VideoClient(CanvasStub(), "123", transport=httpx.MockTransport(handler)) as video:
        video._jwt = "fake"
        with pytest.raises(CanvasError, match="跳转"):
            video.get("/lms/launch-context")
    assert calls == ["v.sjtu.edu.cn"]


def test_missing_transcript_is_not_login_error():
    assert api_data({"ok": True, "dataOk": False, "data": None}) is None
    data = normalize_transcript(None, course_id="123", record=RECORD)
    assert data["status"] == "unavailable" and data["partial"]
    with pytest.raises(CanvasError):
        api_data({"ok": False, "data": None})


def test_video_pagination_and_course_membership():
    pages = []
    with VideoClient(CanvasStub(), "123", transport=httpx.MockTransport(lambda _: None)) as video:
        video._class_id = "15"

        def get(path, params):
            pages.append(params["page.pageIndex"])
            return {"records": [{"id": params["page.pageIndex"]}], "total": 2}

        video.get = get
        assert [r["id"] for r in video.records()] == [1, 2]
        assert pages == [1, 2]
        with pytest.raises(CanvasError, match="不在当前课程"):
            video.record("999")


def test_duplicate_video_page_not_silently_accepted():
    with VideoClient(CanvasStub(), "123", transport=httpx.MockTransport(lambda _: None)) as video:
        video._class_id = "15"
        video.get = lambda *args: {"records": [{"id": 1}], "total": 2}
        with pytest.raises(CanvasError, match="重复分页"):
            video.records()


def test_transcript_raw_data_and_export_boundaries():
    data = sample()
    assert data["raw"]["beforeAssemblyList"][1]["ed"] == 2000
    srt = render_transcript(data, "srt")
    assert "00:00:01,000 --> 00:00:02,000" in srt
    assert "00:00:02,000 --> 00:00:04,000" in srt
    assert render_transcript(data, "vtt").startswith("WEBVTT\n\n")
    assert "[00:00:01.000]" in render_transcript(data, "txt")
    assert search_transcript(data, "cmos")[0]["start_ms"] == 1000


def test_malformed_transcript_reports_gaps():
    data = normalize_transcript(
        {
            "beforeAssemblyList": [
                {"bg": "NaN", "ed": 1000, "res": "文本"},
                {"bg": 10, "ed": 20, "res": "有效"},
            ]
        },
        course_id="123",
        record=RECORD,
    )
    assert len(data["sentences"]) == 1 and data["partial"]
    with pytest.raises(CanvasError):
        normalize_transcript({"unexpected": []}, course_id="123", record=RECORD)


def test_date_filters_and_latest():
    rows = [RECORD, {**RECORD, "id": 8, "courBeginTime": "2026-11-01 09:00:00"}]
    assert select_recordings(rows, latest=1)[0]["id"] == 8
    assert select_recordings(rows, until="2026-10-15")[0]["id"] == 7
    with pytest.raises(ValueError):
        select_recordings(rows, since="2026-13-01")
    with pytest.raises(ValueError):
        select_recordings(rows, since="2026-11-01", until="2026-10-01")


def test_cli_batch_idempotency_and_conflict(tmp_path, monkeypatch):
    data = sample()
    data["partial"], data["warnings"] = False, []

    class FakeVideo:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def records(self):
            return [RECORD]

        def transcript(self, rid):
            return data

    monkeypatch.setattr(cli, "client", lambda: nullcontext(None))
    monkeypatch.setattr(video_cli, "VideoClient", FakeVideo)
    runner = CliRunner()
    args = ["--json", "videos", "subtitles", "123", "--output", str(tmp_path), "--formats", "txt,srt"]
    first = runner.invoke(cli.app, args)
    assert first.exit_code == 0, first.output
    second = runner.invoke(cli.app, args)
    artifacts = json.loads(second.stdout)["data"]["results"][0]["artifacts"]
    assert all(a["status"] == "unchanged" for a in artifacts)
    next(tmp_path.glob("*.txt")).write_text("my notes", encoding="utf-8")
    conflict = runner.invoke(cli.app, args)
    assert conflict.exit_code == 3
    assert next(tmp_path.glob("*.txt")).read_text("utf-8") == "my notes"


def test_url_fragment_token_parsing():
    assert token_from_url(VIDEO_ORIGIN + "/ui/#/page?jwt_token=abc%2Bdef") == "abc+def"
    assert token_from_url(VIDEO_ORIGIN + "/ui") is None


def test_incomplete_video_pagination_is_reported():
    with VideoClient(CanvasStub(), "123", transport=httpx.MockTransport(lambda _: None)) as video:
        video._class_id = "15"
        video.get = lambda path, params: {
            "records": [{"id": 1}] if params["page.pageIndex"] == 1 else [],
            "total": 2,
        }
        with pytest.raises(CanvasError, match="达到总数前中断"):
            video.records()


@pytest.mark.parametrize("payload", [None, {"docList": []}])
def test_missing_slides_report_partial(payload):
    with VideoClient(CanvasStub(), "123", transport=httpx.MockTransport(lambda _: None)) as video:
        video._records = [RECORD]
        video.get = lambda *args: payload
        result = video.slides("7")
        assert result["status"] == "unavailable" and result["partial"]
        assert result["data"] == payload
