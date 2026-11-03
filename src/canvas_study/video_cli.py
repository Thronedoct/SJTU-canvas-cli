"""Composable video discovery, transcript retrieval, export and search commands."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from .client import CanvasError
from .storage import CourseLock, atomic_write, safe_name
from .transcripts import (
    recording_summary,
    render_transcript,
    search_transcript,
    select_recordings,
    validate_selection,
)
from .video import VideoClient

FORMATS = {"json", "txt", "srt", "vtt"}


def register(app, *, get_client, course_id, emit, guarded, is_json, console):
    group = typer.Typer(no_args_is_help=True, help="课堂视频、语音文字稿、字幕、检索与投影截图 OCR。")
    app.add_typer(group, name="videos")

    @group.command("tools")
    @guarded
    def tools(course: str, output: Path | None = typer.Option(None, "--output", "-o")):
        """查看课程的外部工具入口，便于指定课堂视频工具 ID。"""
        with get_client() as c, VideoClient(c, course_id(course)) as v:
            rows = [{k: r.get(k) for k in ("id", "name", "url", "domain")} for r in v.tools()]
        emit(rows, output)

    @group.command("list")
    @guarded
    def listing(
        course: str,
        since: str | None = None,
        until: str | None = None,
        latest: int | None = typer.Option(None, min=1),
        raw: bool = False,
        tool_id: str | None = None,
        output: Path | None = typer.Option(None, "--output", "-o"),
    ):
        """列出课堂视频；支持日期范围和最近 N 节，默认返回简洁元数据。"""
        validate_selection(since, until, latest)
        with get_client() as c, VideoClient(c, course_id(course), tool_id=tool_id) as v:
            rows = select_recordings(v.records(), since, until, latest)
        emit(rows if raw else [recording_summary(r) for r in rows], output)

    @group.command("get")
    @guarded
    def get(
        course: str,
        recording_id: str,
        tool_id: str | None = None,
        output: Path | None = typer.Option(None, "--output", "-o"),
    ):
        """读取当前课程内一节视频的原始平台元数据。"""
        with get_client() as c, VideoClient(c, course_id(course), tool_id=tool_id) as v:
            data = v.record(recording_id)
        emit(data, output)

    @group.command("transcript")
    @guarded
    def transcript(
        course: str,
        recording_id: str,
        fmt: str = typer.Option("json", "--format", "-f"),
        tool_id: str | None = None,
        output: Path | None = typer.Option(None, "--output", "-o"),
    ):
        """获取平台已有文字稿；默认 JSON 保留时间戳、整理段落及原始数据。"""
        if fmt not in FORMATS:
            raise ValueError("--format 应为 json / txt / srt / vtt")
        with get_client() as c, VideoClient(c, course_id(course), tool_id=tool_id) as v:
            data = v.transcript(recording_id)
        if fmt == "json":
            if is_json():
                emit(data, output)
            else:
                if output:
                    atomic_write(
                        output.expanduser().resolve(), json.dumps(data, ensure_ascii=False, indent=2) + "\n"
                    )
                console.print(
                    f"{data['recording']['title']} · {data['recording'].get('begin') or ''}", markup=False
                )
                console.print(
                    f"状态：{data['status']}；语音片段 {len(data['sentences'])}，整理段落 {len(data['paragraphs'])}",
                    markup=False,
                )
                console.print(
                    f"已保存：{output.resolve()}"
                    if output
                    else "使用 --json 查看完整结构，或 --format txt --output PATH 保存文字稿。",
                    markup=False,
                )
                for warning in data["warnings"]:
                    console.print(warning, markup=False)
        elif data["status"] != "available":
            emit(data, None)
        else:
            text = render_transcript(data, fmt)
            if output:
                atomic_write(output.expanduser().resolve(), text)
            if is_json():
                emit(
                    {
                        "recording": data["recording"],
                        "format": fmt,
                        "path": str(output.resolve()) if output else None,
                        "content": text if not output else None,
                        "partial": data["partial"],
                        "warnings": data["warnings"],
                    },
                    None,
                )
            elif output:
                console.print(f"已保存：{output.resolve()}", markup=False)
            else:
                typer.echo(text, nl=False)
        if data["partial"]:
            raise typer.Exit(3)

    @group.command("subtitles")
    @guarded
    def subtitles(
        course: str,
        output: Path = typer.Option(..., "--output", "-o", help="字幕保存目录"),
        formats: str = "json,txt,srt",
        since: str | None = None,
        until: str | None = None,
        latest: int | None = typer.Option(None, min=1),
        overwrite: bool = False,
        tool_id: str | None = None,
    ):
        """按节次批量保存文字稿与字幕；同内容跳过，覆盖变更需 --overwrite。"""
        kinds = list(dict.fromkeys(f.strip() for f in formats.split(",")))
        if not kinds or not set(kinds) <= FORMATS:
            raise ValueError("--formats 应为 json,txt,srt,vtt 的组合")
        validate_selection(since, until, latest)
        destination = output.expanduser().resolve()
        results, warnings = [], []
        with get_client() as c, VideoClient(c, course_id(course), tool_id=tool_id) as v:
            rows = select_recordings(v.records(), since, until, latest)
            with CourseLock(destination):
                for row in rows:
                    rid = str(row["id"])
                    try:
                        data = v.transcript(rid)
                        if data["status"] != "available":
                            results.append({"id": rid, "status": "unavailable", "warnings": data["warnings"]})
                            continue
                        name = safe_name(
                            f"{row.get('courBeginTime') or ''}_{row.get('subjName') or '课堂视频'}_{rid}", 110
                        )
                        artifacts = []
                        for kind in kinds:
                            path = destination / f"{name}.{kind}"
                            content = (
                                (json.dumps(data, ensure_ascii=False, indent=2) + "\n")
                                if kind == "json"
                                else render_transcript(data, kind)
                            )
                            if path.exists():
                                if path.read_text(encoding="utf-8") == content:
                                    artifacts.append({"path": str(path), "status": "unchanged"})
                                    continue
                                if not overwrite:
                                    artifacts.append({"path": str(path), "status": "conflict"})
                                    continue
                            atomic_write(path, content)
                            artifacts.append({"path": str(path), "status": "written"})
                        results.append(
                            {
                                "id": rid,
                                "status": "partial"
                                if data["partial"] or any(a["status"] == "conflict" for a in artifacts)
                                else "complete",
                                "artifacts": artifacts,
                                "warnings": data["warnings"],
                            }
                        )
                    except CanvasError as error:
                        if error.status == 401:
                            raise
                        results.append({"id": rid, "status": "failed", "message": str(error)})
                    if not is_json():
                        console.print(f"{rid}：{results[-1]['status']}", markup=False)
        if not results:
            warnings.append("所选范围内没有课堂视频")
        partial = bool(warnings) or any(r["status"] != "complete" for r in results)
        emit(
            {
                "course_id": course_id(course),
                "output": str(destination),
                "results": results,
                "partial": partial,
                "warnings": warnings,
            },
            None,
        )
        if partial:
            raise typer.Exit(3)

    @group.command("search")
    @guarded
    def search(
        course: str,
        query: str,
        since: str | None = None,
        until: str | None = None,
        latest: int | None = typer.Option(None, min=1),
        tool_id: str | None = None,
        output: Path | None = typer.Option(None, "--output", "-o"),
    ):
        """按关键词检索所选课堂文字稿，返回可定位的时间戳；不改写原文。"""
        if not query.strip():
            raise ValueError("检索词不能为空")
        validate_selection(since, until, latest)
        matches, unavailable, warnings = [], [], []
        with get_client() as c, VideoClient(c, course_id(course), tool_id=tool_id) as v:
            rows = select_recordings(v.records(), since, until, latest)
            for row in rows:
                try:
                    data = v.transcript(str(row["id"]))
                    matches.extend(search_transcript(data, query))
                    if data["partial"]:
                        unavailable.append(str(row["id"]))
                        warnings.extend(data["warnings"])
                except CanvasError as error:
                    if error.status == 401:
                        raise
                    unavailable.append(str(row["id"]))
                    warnings.append(str(error))
        if not rows:
            warnings.append("所选范围内没有课堂视频")
        partial = bool(unavailable or warnings)
        emit(
            {
                "query": query,
                "recordings_examined": len(rows),
                "matches": matches,
                "unavailable_recording_ids": unavailable,
                "partial": partial,
                "warnings": list(dict.fromkeys(warnings)),
            },
            output,
        )
        if partial:
            raise typer.Exit(3)

    @group.command("slides")
    @guarded
    def slides(
        course: str,
        recording_id: str,
        tool_id: str | None = None,
        output: Path | None = typer.Option(None, "--output", "-o"),
    ):
        """读取平台已有投影截图时间点、图片链接与 OCR 数据；不下载视频。"""
        with get_client() as c, VideoClient(c, course_id(course), tool_id=tool_id) as v:
            data = v.slides(recording_id)
        emit(data, output)
        if data["partial"]:
            raise typer.Exit(3)
