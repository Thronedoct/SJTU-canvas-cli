"""Transcript data normalization and deterministic subtitle formats; no network or file I/O."""

from __future__ import annotations

import math
from datetime import date

from .client import CanvasError


def recording_summary(record: dict) -> dict:
    return {
        "id": str(record["id"]),
        "title": record.get("subjName") or record.get("teclName") or "课堂视频",
        "begin": record.get("courBeginTime"),
        "end": record.get("courEndTime"),
        "teacher": record.get("tecName") or record.get("teclTeacNames") or record.get("teacNames"),
        "classroom": record.get("clroName"),
        "week": record.get("weekNumber") or record.get("weekNo"),
        "period": record.get("letiNumber"),
        "transcript_status_code": record.get("transcriptOptimizeTaskStatus"),
    }


def validate_selection(since: str | None, until: str | None, latest: int | None):
    try:
        if since:
            date.fromisoformat(since)
        if until:
            date.fromisoformat(until)
    except ValueError:
        raise ValueError("日期应为 YYYY-MM-DD") from None
    if since and until and since > until:
        raise ValueError("--since 不能晚于 --until")
    if latest is not None and latest < 1:
        raise ValueError("--latest 必须大于 0")


def select_recordings(records: list, since=None, until=None, latest=None):
    validate_selection(since, until, latest)
    selected = [
        r
        for r in records
        if (not since or (r.get("courBeginTime") or "")[:10] >= since)
        and (not until or (r.get("courBeginTime") or "")[:10] <= until)
    ]
    selected.sort(key=lambda r: (r.get("courBeginTime") or "", str(r["id"])), reverse=True)
    return selected[:latest] if latest else selected


def normalize_transcript(data, *, course_id: str, record: dict):
    warnings = []
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise CanvasError("语音文字稿格式发生变化")
    if data and not any(k in data for k in ("beforeAssemblyList", "afterAssemblyList")):
        raise CanvasError("语音文字稿缺少预期的片段字段")

    def parse(key):
        rows = data.get(key) or []
        if not isinstance(rows, list):
            raise CanvasError("语音文字稿片段格式发生变化")
        result = []
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                warnings.append(f"{key}[{index}] 不是有效片段")
                continue
            text = str(row.get("res") or row.get("zh") or "").strip()
            if not text:
                continue
            try:
                begin, end = float(row["bg"]), float(row["ed"])
                if not math.isfinite(begin) or not math.isfinite(end) or begin < 0:
                    raise ValueError
            except (KeyError, TypeError, ValueError):
                warnings.append(f"{key}[{index}] 时间戳不可用；原记录保留在 raw 字段")
                continue
            if end <= begin:
                warnings.append(f"{key}[{index}] 结束时间不大于开始时间，字幕导出时修正")
            result.append({"start_ms": int(begin), "end_ms": int(end), "text": text, "source_index": index})
        return sorted(result, key=lambda line: line["start_ms"])

    sentences, paragraphs = parse("beforeAssemblyList"), parse("afterAssemblyList")
    available = bool(sentences or paragraphs)
    if not available:
        warnings.append("平台暂未提供文字稿：可能尚未生成、未发布或当前账号不可访问；未自行触发转写")
    return {
        "schema_version": 1,
        "course_id": str(course_id),
        "recording": recording_summary(record),
        "source": "sjtu_video_asr",
        "status": "available" if available else "unavailable",
        "time_unit": "milliseconds",
        "sentences": sentences,
        "paragraphs": paragraphs,
        "raw": data,
        "partial": bool(warnings),
        "warnings": warnings,
    }


def timestamp(ms: int, *, comma=False) -> str:
    hours, tail = divmod(max(0, ms), 3600000)
    minutes, tail = divmod(tail, 60000)
    seconds, fraction = divmod(tail, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{',' if comma else '.'}{fraction:03d}"


def render_transcript(transcript: dict, fmt: str) -> str:
    if fmt not in {"txt", "srt", "vtt"}:
        raise ValueError("文字格式应为 txt / srt / vtt")
    rows = (
        (transcript["paragraphs"] or transcript["sentences"])
        if fmt == "txt"
        else (transcript["sentences"] or transcript["paragraphs"])
    )
    if fmt == "txt":
        recording = transcript["recording"]
        header = f"{recording['title']} | {recording.get('begin') or ''}\n来源：课堂视频平台自动语音识别；可能有错字。\n"
        return header + "\n" + "\n\n".join(f"[{timestamp(r['start_ms'])}] {r['text']}" for r in rows) + "\n"
    cues = []
    for i, row in enumerate(rows):
        start, end = row["start_ms"], row["end_ms"]
        next_start = rows[i + 1]["start_ms"] if i + 1 < len(rows) else None
        if end <= start:
            end = start + 2000
        if next_start is not None and next_start > start:
            end = min(end, next_start)
        text = row["text"].replace("\r", "").replace("\n\n", "\n")
        if fmt == "vtt":
            text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        cues.append(
            f"{i + 1}\n{timestamp(start, comma=fmt == 'srt')} --> {timestamp(end, comma=fmt == 'srt')}\n{text}\n"
        )
    return ("WEBVTT\n\n" if fmt == "vtt" else "") + "\n".join(cues)


def search_transcript(transcript: dict, query: str) -> list:
    if not query.strip():
        raise ValueError("检索词不能为空")
    rows = transcript["paragraphs"] or transcript["sentences"]
    return [
        {
            "recording_id": transcript["recording"]["id"],
            "title": transcript["recording"]["title"],
            "begin": transcript["recording"].get("begin"),
            "start_ms": r["start_ms"],
            "end_ms": r["end_ms"],
            "timestamp": timestamp(r["start_ms"]),
            "text": r["text"],
        }
        for r in rows
        if query.casefold() in r["text"].casefold()
    ]
