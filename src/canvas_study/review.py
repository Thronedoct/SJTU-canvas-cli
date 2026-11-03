from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

from bs4 import BeautifulSoup, NavigableString
from markdownify import markdownify
from pypdf import PdfReader

from .download import verified
from .resources import questions_from_html
from .storage import atomic_write, read_json, under


def markdown(html: str, local_files: dict[str, str] | None = None) -> str:
    soup = BeautifulSoup(html or "", "html.parser")
    math = {}
    for node in soup.select("script[type^='math/tex'], img.equation_image, [data-equation-content], math"):
        if node.name == "math":
            annotation = node.find("annotation", encoding="application/x-tex")
            latex = annotation.get_text() if annotation else node.get_text(" ")
        else:
            latex = node.get("data-equation-content") or node.get("alt") or node.get_text()
        latex = unquote(latex).strip().strip("$")
        key = f"CANVASMATHPLACEHOLDER{len(math)}END"
        math[key] = "$" + latex + "$"
        node.replace_with(NavigableString(key))
    for node in soup.select("script, style, iframe, form, object, embed"):
        node.decompose()
    for node in soup.select("[href], [src]"):
        for attr in ("href", "src"):
            url = node.get(attr)
            if not url:
                continue
            match = re.search(r"/files/(\d+)(?:/|$)", urlsplit(url).path)
            if match and match[1] in (local_files or {}):
                node[attr] = quote(local_files[match[1]], safe="/._-")
            elif urlsplit(url).scheme not in {"https", "http", ""}:
                del node[attr]
    result = markdownify(str(soup), heading_style="ATX", bullets="-", escape_underscores=False)
    for key, value in math.items():
        result = result.replace(key, value)
    return result.strip()


def pdf_text(path: Path) -> tuple[str, list[str]]:
    sections, warnings = [], []
    reader = PdfReader(path)
    for index, page in enumerate(reader.pages, 1):
        text = (page.extract_text() or "").strip()
        if len(text) < 20:
            warnings.append(f"第 {index} 页文字较少，可能是扫描页，需要 OCR 或查看原件")
        sections.append(f"##### 第 {index} 页\n\n" + (text or "（未提取到文字）"))
    return "\n\n".join(sections), warnings


def generate_review(catalog: dict, root: Path, *, output: Path | None = None, html_dir: Path | None = None):
    output = output or root / "复习" / "作业题目汇总.md"
    state = read_json(root / ".canvas" / "manifest.json", {"files": {}})
    local, local_paths, issues = {}, {}, list(catalog.get("warnings", []))
    remote_files = {f["id"]: f for f in catalog.get("files", [])}
    for fid, entry in state["files"].items():
        path = under(root, entry["path"])
        if fid in remote_files and verified(root, entry, remote_files[fid]):
            local[fid] = Path(os.path.relpath(path, output.parent)).as_posix()
            local_paths[fid] = path
    lines = [
        f"# {catalog['course_name']} · 作业题目汇总",
        "",
        f"来源：[Canvas 课程]({catalog['base_url']}/courses/{catalog['course_id']})  ",
        f"采集时间：{catalog['fetched_at']}  ",
        "整理原则：保留原题和选项；不推断答案；附加计算题保留整份附件文字，不擅自删选。",
        "",
        "PDF 的公式、图和表格仍须对照链接中的原件；扫描件尚未执行 OCR。",
        "",
        "## 目录",
        "",
    ]
    docs = sorted(catalog["assignments"], key=lambda a: (a.get("due_at") or "9999", a["id"]))
    for doc in docs:
        lines.append(f"- [{doc['title']}](#assignment-{doc['id']})")
    count = 0
    for doc in docs:
        lines += [
            "",
            f'<a id="assignment-{doc["id"]}"></a>',
            "",
            f"## {doc['title']}",
            "",
            f"[查看原作业]({doc['url']}) · 截止：{doc.get('due_at') or '未设置'}",
            "",
        ]
        if doc.get("survey"):
            lines += ["> 本项为反馈问卷，单独标注以便复习时跳过。", ""]
        lines += [markdown(doc.get("description", ""), local) or "（页面未提供文字说明）", ""]
        rows = doc.get("questions", [])
        if html_dir and (html_dir / f"{doc['id']}.html").is_file():
            imported = questions_from_html((html_dir / f"{doc['id']}.html").read_text(encoding="utf-8"))
            if imported:
                rows = imported
                lines += ["> 题目来源：用户保存的 Canvas 结果页。", ""]
        if doc.get("quiz_id"):
            expected = doc.get("expected_questions")
            if not rows or (expected and len(rows) != expected):
                reason = doc.get("question_note", f"返回 {len(rows)} 题，目录标示 {expected or '未知'} 题")
                issues.append(f"{doc['title']}：题目未完整取得（{reason}）")
                lines += [
                    f"> **题目未完整取得**：{reason}。",
                    "> 可以将可见的结果页保存为 HTML，再用 review --html-dir 导入。",
                    "",
                ]
            for i, question in enumerate(sorted(rows, key=lambda q: q.get("position", 0)), 1):
                if not BeautifulSoup(question["body"], "html.parser").get_text().strip() and not re.search(
                    r"<(img|math)|data-equation-content", question["body"]
                ):
                    issues.append(f"{doc['title']} / 题 {i}：题干为空")
                if question.get("type") in {
                    "matching_question",
                    "fill_in_multiple_blanks_question",
                    "multiple_dropdowns_question",
                    "calculated_question",
                }:
                    issues.append(f"{doc['title']} / 题 {i}：复杂题型，请核对原页面的配对项或变量")
                for content in [question["body"], *question.get("answers", [])]:
                    for img in BeautifulSoup(content, "html.parser").select("img:not(.equation_image)"):
                        match = re.search(r"/files/(\d+)", img.get("src", ""))
                        if not match or match[1] not in local:
                            issues.append(f"{doc['title']} / 题 {i}：图片尚未本地化，需联网查看原页面")
                lines += [f"### 题 {i}", "", markdown(question["body"], local), ""]
                for n, answer in enumerate(question.get("answers", []), 1):
                    label = chr(64 + n) if n <= 26 else str(n)
                    lines += [f"- **{label}.** " + markdown(answer, local)]
                lines.append("")
                count += 1
        for fid in doc.get("attachments", []):
            path = local_paths.get(fid)
            if not path:
                issues.append(f"{doc['title']}：附件 {fid} 未下载或未通过校验")
                lines += [f"> 附件 {fid} 尚未取得，请完成同步后重新生成。", ""]
                continue
            link = quote(local[fid], safe="/._-")
            lines += [f"[附件原件：{path.name}]({link})", ""]
            if path.suffix.lower() == ".pdf":
                try:
                    text, notes = pdf_text(path)
                    lines += [
                        "<details>",
                        "<summary>展开附件文字（图表和公式请核对原件）</summary>",
                        "",
                        text,
                        "",
                        "</details>",
                        "",
                    ]
                    issues.extend(f"{doc['title']} / {path.name}：{note}" for note in notes)
                except Exception:
                    issues.append(f"{doc['title']}：PDF 无法解析，请查看附件原件")
    lines += ["## 完整性检查", "", f"共 {len(docs)} 项作业 / 测验，取得 {count} 道结构化题目。", ""]
    issues = list(dict.fromkeys(issues))
    lines += (
        ["- " + item for item in issues]
        if issues
        else ["- 当前已采集范围内未发现缺失；PDF 图表及公式仍需核对原件。"]
    )
    atomic_write(output, "\n".join(lines).rstrip() + "\n")
    return {"path": str(output), "assignments": len(docs), "questions": count, "issues": issues}
