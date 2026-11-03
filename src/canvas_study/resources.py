"""Reusable Canvas resource operations. No filesystem, Markdown or course binding dependencies."""

import re
from urllib.parse import quote, urljoin, urlsplit

from bs4 import BeautifulSoup

from .client import CanvasError, origin


def file_ids(html: str, base: str) -> list[str]:
    result = set()
    for node in BeautifulSoup(html or "", "html.parser").select("[href], [src], [data-api-endpoint]"):
        for attr in ("href", "src", "data-api-endpoint"):
            url = urljoin(base, node.get(attr, ""))
            if origin(url) == origin(base):
                match = re.search(r"/files/(\d+)(?:/|$)", urlsplit(url).path)
                if match:
                    result.add(match.group(1))
    return sorted(result, key=int)


def resource_id(value: str) -> str:
    value = str(value)
    if not value.isascii() or not value.isdecimal():
        raise ValueError("资源 ID 必须是数字")
    return value


def resource_path(course: str, resource: str, item: str | None = None) -> str:
    if resource not in {"assignments", "quizzes", "modules", "pages", "files"}:
        raise ValueError("不支持的资源类型")
    path = f"/api/v1/courses/{resource_id(course)}/{resource}"
    if item is not None:
        path += "/" + (quote(item, safe="") if resource == "pages" else resource_id(item))
    return path


def questions_from_html(text: str) -> list:
    """Parse visible question markup independently from any export format."""
    soup = BeautifulSoup(text, "html.parser")
    result = []
    for i, question in enumerate(soup.select(".question"), 1):
        body = question.select_one(".question_text")
        if body:
            result.append(
                {
                    "id": question.get("id", str(i)),
                    "position": i,
                    "body": str(body),
                    "answers": [str(a) for a in question.select(".answer .answer_text")],
                    "type": "html",
                }
            )
    return result


def fetch_questions(client, course: str, quiz: str, *, html_url: str = "", expected=None) -> dict:
    """Preserve API fields; HTML fallback uses Canvas-like question keys and declares its source."""
    resource_id(course)
    resource_id(quiz)
    source, rows, warnings = "canvas_api", [], []
    try:
        rows = client.quiz_questions(course, quiz)
    except CanvasError as error:
        if error.status == 401:
            # Canvas may return 401 for a student-forbidden questions API even
            # while the session and visible result page remain valid.
            if not client.auth.get("cookies") or not html_url:
                raise
            if not client.get("/api/v1/users/self/profile").get("id"):
                raise
            warnings.append("题目 API 无权读取，尝试账号可见的结果页")
        else:
            warnings.append(str(error))
        if client.auth.get("cookies") and html_url:
            try:
                response = client.request(html_url)
                try:
                    imported = questions_from_html(response.text)
                finally:
                    response.close()
                if imported:
                    source = "result_html"
                    rows = [
                        {
                            "id": q["id"],
                            "position": q["position"],
                            "question_type": "unknown",
                            "question_text": q["body"],
                            "answers": [{"html": a} for a in q["answers"]],
                        }
                        for q in imported
                    ]
                    warnings = []
            except CanvasError as fallback_error:
                if fallback_error.status == 401:
                    raise
                warnings.append(str(fallback_error))
    partial = not rows or (expected is not None and len(rows) != expected)
    if partial and not warnings:
        warnings.append(f"取得 {len(rows)} 题，预期 {expected if expected is not None else '未知'} 题")
    return {
        "course_id": str(course),
        "quiz_id": str(quiz),
        "source": source,
        "items": rows,
        "expected_count": expected,
        "partial": partial,
        "warnings": warnings,
    }


def assignment_attachments(client, course: str, assignment: dict) -> dict:
    rows, warnings = [], []
    for fid in file_ids(assignment.get("description") or "", client.base):
        try:
            rows.append(client.get(resource_path(course, "files", fid)))
        except CanvasError as error:
            if error.status == 401:
                raise
            warnings.append({"file_id": fid, "message": str(error)})
    return {"items": rows, "partial": bool(warnings), "warnings": warnings}


def fetch_assignment(client, course: str, assignment_id: str, *, questions=False, submission=False) -> dict:
    path = resource_path(course, "assignments", assignment_id)
    assignment = client.get(path)
    attachments = assignment_attachments(client, course, assignment)
    result = {
        "assignment": assignment,
        "attachments": attachments,
        "questions": None,
        "submission": None,
        "partial": attachments["partial"],
        "warnings": [],
    }
    if questions and assignment.get("quiz_id"):
        try:
            quiz = client.get(resource_path(course, "quizzes", str(assignment["quiz_id"])))
            result["questions"] = fetch_questions(
                client,
                course,
                str(quiz["id"]),
                html_url=assignment.get("html_url") or quiz.get("html_url", ""),
                expected=quiz.get("question_count"),
            )
            result["partial"] |= result["questions"]["partial"]
        except CanvasError as error:
            if error.status == 401:
                raise
            result["partial"] = True
            result["warnings"].append({"resource": "questions", "message": str(error)})
    if submission:
        try:
            result["submission"] = client.get(path + "/submissions/self")
        except CanvasError as error:
            if error.status == 401:
                raise
            result["partial"] = True
            result["warnings"].append({"resource": "submission", "message": str(error)})
    return result
