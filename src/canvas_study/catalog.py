from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import quote, urljoin, urlsplit

from bs4 import BeautifulSoup

from .client import CanvasError
from .resources import fetch_questions, file_ids


def clean_html(html: str, base: str) -> str:
    soup = BeautifulSoup(html or "", "html.parser")
    for node in soup.select("script[type^='math/tex']"):
        replacement = soup.new_tag("span")
        replacement["data-equation-content"] = node.get_text()
        node.replace_with(replacement)
    for node in soup.select("script, style, iframe, object, embed, form"):
        node.decompose()
    for node in soup.find_all(True):
        for attr in list(node.attrs):
            if attr.startswith("on"):
                del node[attr]
        for attr in ("href", "src"):
            if node.get(attr):
                url = urljoin(base, node[attr])
                if urlsplit(url).scheme not in {"http", "https"}:
                    del node[attr]
                else:
                    # Avoid retaining expiring bearer-like query values in exports.
                    node[attr] = url.split("?")[0].split("#")[0]
    return str(soup)


def discover(client, course_id: str, *, questions: bool = False) -> dict:
    base = f"/api/v1/courses/{course_id}"
    course = client.get(base)
    warnings, files, references = [], {}, {}

    def optional(fn, label, default):
        try:
            return fn()
        except CanvasError as error:
            if error.status == 401:
                raise
            warnings.append(f"{label}：{error}")
            return default

    def add_ref(fid, label):
        references.setdefault(str(fid), [])
        if label not in references[str(fid)]:
            references[str(fid)].append(label)

    listed = optional(lambda: client.list(base + "/files"), "文件库未完整读取", [])
    for file in listed:
        files[str(file["id"])] = file
    modules = optional(lambda: client.list(base + "/modules"), "单元目录未完整读取", [])
    for index, module in enumerate(modules, 1):
        label = f"{index:02d}_{module.get('name', '单元')}"
        items = optional(lambda m=module: client.list(base + f"/modules/{m['id']}/items"), label, [])
        for item in items:
            if item.get("type") == "File" and item.get("content_id"):
                add_ref(item["content_id"], label)
            elif item.get("type") == "Page" and item.get("page_url"):
                page = optional(
                    lambda it=item: client.get(base + "/pages/" + quote(it["page_url"], safe="")),
                    "单元页面 " + item.get("title", ""),
                    {},
                )
                for fid in file_ids(page.get("body", ""), client.base):
                    add_ref(fid, label)
    for fid in file_ids(course.get("syllabus_body", ""), client.base):
        add_ref(fid, "课程大纲")
    assignments = optional(
        lambda: client.list(base + "/assignments", {"order_by": "due_at"}), "作业目录未完整读取", []
    )
    quizzes = optional(lambda: client.list(base + "/quizzes"), "测验目录未完整读取", [])
    quiz_map = {str(q["id"]): q for q in quizzes}
    assigned = {str(a.get("quiz_id")) for a in assignments}
    for quiz in quizzes:
        if str(quiz["id"]) not in assigned:
            assignments.append(
                {
                    "id": "quiz-" + str(quiz["id"]),
                    "name": quiz["title"],
                    "quiz_id": quiz["id"],
                    "description": quiz.get("description", ""),
                    "html_url": quiz.get("html_url"),
                    "due_at": quiz.get("due_at"),
                }
            )
    documents = []
    for assignment in assignments:
        html = assignment.get("description") or ""
        attached = file_ids(html, client.base)
        for fid in attached:
            add_ref(fid, "作业附件")
        quiz_id = str(assignment.get("quiz_id") or "")
        quiz = quiz_map.get(quiz_id, {})
        doc = {
            "id": str(assignment["id"]),
            "title": assignment.get("name", "未命名作业"),
            "url": assignment.get("html_url", ""),
            "due_at": assignment.get("due_at"),
            "description": clean_html(html, client.base),
            "attachments": attached,
            "quiz_id": quiz_id,
            "questions": [],
            "question_status": "not_applicable",
            "expected_questions": quiz.get("question_count"),
            "survey": "survey" in quiz.get("quiz_type", "") or "问卷" in assignment.get("name", ""),
        }
        if quiz_id:
            doc["question_status"] = "not_requested"
            if questions:
                result = fetch_questions(
                    client, course_id, quiz_id, html_url=doc["url"], expected=doc["expected_questions"]
                )
                doc["questions"] = [
                    {
                        "id": str(q["id"]),
                        "position": q.get("position", i),
                        "type": q.get("question_type", ""),
                        "body": clean_html(q.get("question_text", ""), client.base),
                        "answers": [
                            clean_html(
                                a.get("html")
                                or a.get("answer_html")
                                or a.get("text")
                                or a.get("answer_text")
                                or "",
                                client.base,
                            )
                            for a in q.get("answers") or []
                        ],
                    }
                    for i, q in enumerate(result["items"], 1)
                ]
                doc["question_status"] = (
                    "unavailable"
                    if result["partial"]
                    else "available_html"
                    if result["source"] == "result_html"
                    else "available"
                )
                if result["warnings"]:
                    doc["question_note"] = "; ".join(result["warnings"])
                if result["partial"]:
                    warnings.append(f"测验 {doc['title']}：{doc.get('question_note', '题目未完整取得')}")
                for question in doc["questions"]:
                    for part in [question["body"], *question["answers"]]:
                        for fid in file_ids(part, client.base):
                            add_ref(fid, "题目图片")
                            if fid not in doc["attachments"]:
                                doc["attachments"].append(fid)
        documents.append(doc)
    for fid in references:
        if fid not in files:
            file = optional(lambda f=fid: client.get(base + f"/files/{f}"), f"文件 {fid}", None)
            if file:
                files[fid] = file
    normalized = []
    for fid, file in files.items():
        normalized.append(
            {
                "id": fid,
                "name": file.get("display_name") or file.get("filename") or fid,
                "size": file.get("size"),
                "updated_at": file.get("updated_at", ""),
                "locked": bool(file.get("locked_for_user")),
                "sources": references.get(fid, ["文件库"]),
                "content_type": file.get("content-type", ""),
            }
        )
    return {
        "schema_version": 1,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "base_url": client.base,
        "course_id": course_id,
        "course_name": course.get("name", course_id),
        "files": normalized,
        "assignments": documents,
        "warnings": warnings,
    }
