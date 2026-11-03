"""Resource-oriented commands, registered with the shared CLI context."""

from pathlib import Path

import typer

from .download import download_one
from .resources import assignment_attachments, fetch_assignment, fetch_questions, resource_id, resource_path


def register(app, *, get_client, course_id, emit, guarded):
    groups = {}

    def list_command(kind):
        @guarded
        def listing(
            course: str,
            search: str = typer.Option("", help="按名称筛选，大小写不敏感"),
            output: Path | None = typer.Option(None, "--output", "-o", help="另存原始 JSON"),
        ):
            """列出全部分页结果；仅获取数据，不下载附件。"""
            with get_client() as c:
                rows = c.list(resource_path(course_id(course), kind))
            if search:
                rows = [
                    r
                    for r in rows
                    if search.casefold()
                    in str(r.get("name") or r.get("title") or r.get("display_name") or "").casefold()
                ]
            emit(rows, output)

        return listing

    def get_command(kind):
        @guarded
        def get(
            course: str,
            item_id: str,
            output: Path | None = typer.Option(None, "--output", "-o", help="另存原始 JSON"),
        ):
            """读取单项详情，保留原始字段和 HTML；pages 使用页面 slug。"""
            with get_client() as c:
                result = c.get(resource_path(course_id(course), kind, item_id))
            emit(result, output)

        return get

    for name, label in {
        "assignments": "作业",
        "quizzes": "测验",
        "modules": "课程单元",
        "pages": "课程页面",
        "files": "课程文件",
    }.items():
        group = typer.Typer(no_args_is_help=True, help=f"获取{label}的列表与详情。")
        group.command("list")(list_command(name))
        if name != "files":
            group.command("get")(get_command(name))
        app.add_typer(group, name=name)
        groups[name] = group

    @groups["assignments"].command("fetch")
    @guarded
    def assignment_fetch(
        course: str,
        assignment_id: str,
        questions: bool = typer.Option(False, help="额外读取可访问的测验题目"),
        submission: bool = typer.Option(False, help="额外读取自己的提交记录"),
        output: Path | None = typer.Option(None, "--output", "-o"),
    ):
        """抓取一份作业及附件元数据；输出结构化数据，不生成笔记。"""
        with get_client() as c:
            result = fetch_assignment(
                c, course_id(course), assignment_id, questions=questions, submission=submission
            )
        emit(result, output)
        if result["partial"]:
            raise typer.Exit(3)

    @groups["assignments"].command("attachments")
    @guarded
    def attachments(
        course: str, assignment_id: str, output: Path | None = typer.Option(None, "--output", "-o")
    ):
        """读取作业说明内引用的 Canvas 附件元数据；下载使用 files download。"""
        with get_client() as c:
            cid = course_id(course)
            assignment = c.get(resource_path(cid, "assignments", assignment_id))
            result = assignment_attachments(c, cid, assignment)
        emit(result, output)
        if result["partial"]:
            raise typer.Exit(3)

    @groups["assignments"].command("submission")
    @guarded
    def submission_get(
        course: str, assignment_id: str, output: Path | None = typer.Option(None, "--output", "-o")
    ):
        """读取自己的提交状态、评分等原始记录。"""
        with get_client() as c:
            result = c.get(
                resource_path(course_id(course), "assignments", assignment_id) + "/submissions/self"
            )
        emit(result, output)

    @groups["quizzes"].command("questions")
    @guarded
    def quiz_questions(course: str, quiz_id: str, output: Path | None = typer.Option(None, "--output", "-o")):
        """读取已完成尝试中的题目；保留 API 结构，标明来源及缺失。"""
        with get_client() as c:
            cid = course_id(course)
            quiz = c.get(resource_path(cid, "quizzes", quiz_id))
            result = fetch_questions(
                c, cid, quiz_id, html_url=quiz.get("html_url", ""), expected=quiz.get("question_count")
            )
        emit(result, output)
        if result["partial"]:
            raise typer.Exit(3)

    @groups["modules"].command("items")
    @guarded
    def module_items(course: str, module_id: str, output: Path | None = typer.Option(None, "--output", "-o")):
        """读取一个单元内的全部条目。"""
        with get_client() as c:
            result = c.list(resource_path(course_id(course), "modules", module_id) + "/items")
        emit(result, output)

    @groups["files"].command("get")
    @guarded
    def file_get(file_id: str, output: Path | None = typer.Option(None, "--output", "-o")):
        """通过文件 ID 读取原始元数据和当前可用下载链接。"""
        with get_client() as c:
            result = c.get(f"/api/v1/files/{resource_id(file_id)}")
        emit(result, output)

    @groups["files"].command("download")
    @guarded
    def file_download(
        file_id: str, output: Path = typer.Option(..., "--output", "-o", help="保存为此文件路径")
    ):
        """下载一个文件，支持续传和校验；不需要课程目录绑定。"""
        target = output.expanduser().resolve()
        if target.exists():
            raise ValueError("目标文件已存在，请指定新路径")
        with get_client() as c:
            remote = c.get(f"/api/v1/files/{resource_id(file_id)}")
            file = {
                "id": str(remote["id"]),
                "size": remote.get("size"),
                "updated_at": remote.get("updated_at", ""),
            }
            result = download_one(c, file, target)
        emit({"file_id": file_id, "path": str(target), **result}, None)
