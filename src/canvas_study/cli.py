from __future__ import annotations

import functools
import getpass
import importlib.util
import json
import os
import sys
from collections import Counter
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .auth import browser_login, load_auth, save_auth
from .catalog import discover
from .client import CanvasClient, CanvasError
from .download import sync_files
from .resource_cli import register
from .video_cli import register as register_videos
from .resources import resource_id
from .storage import CourseLock, home, read_json, save_settings, settings, write_json

app = typer.Typer(
    no_args_is_help=True,
    invoke_without_command=True,
    help="Canvas CLI · 获取课程、作业、测验、文件；为人和脚本提供可组合的数据接口。",
    pretty_exceptions_enable=False,
)
console = Console(stderr=True)
json_mode = False


def emit(data, message=""):
    if json_mode:
        print(json.dumps({"ok": True, "data": data}, ensure_ascii=False))
    elif message:
        console.print(message, markup=False)
    else:
        rows = data if isinstance(data, list) else [data]
        if not rows:
            console.print("暂无记录。")
            return
        table = Table(show_lines=False, header_style="cyan")
        columns = list(rows[0])
        for key in columns:
            table.add_column(key, overflow="fold")
        for row in rows:
            table.add_row(*(str(row.get(key, "")) for key in columns))
        console.print(table)


def guarded(fn):
    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (CanvasError, ValueError, OSError) as error:
            if json_mode:
                print(json.dumps({"ok": False, "error": {"message": str(error)}}, ensure_ascii=False))
            else:
                console.print("未完成：" + str(error), style="red", markup=False)
            raise typer.Exit(2) from None

    return wrapped


@app.callback()
def root(
    as_json: bool = typer.Option(False, "--json", help="输出稳定的 JSON，供脚本 / Agent 使用"),
    config_home: Path | None = typer.Option(None, "--home", help="配置与加密凭据目录"),
    version: bool = typer.Option(False, "--version", is_eager=True),
):
    global json_mode
    json_mode = as_json
    if config_home:
        os.environ["CANVAS_STUDY_HOME"] = str(config_home.resolve())
    if version:
        print(__version__)
        raise typer.Exit()


def client():
    cfg = settings()
    return CanvasClient(cfg["base_url"], load_auth(cfg["base_url"]))


def review_exporter():
    try:
        from .review import generate_review
    except ImportError:
        raise ValueError(
            "此导出需要可选依赖：uv sync --extra export（浏览器登录另加 --extra browser）"
        ) from None
    return generate_review


def course_id(course: str) -> str:
    if course.isascii() and course.isdecimal():
        return course
    matches = [cid for cid, item in settings()["courses"].items() if item.get("name") == course]
    if len(matches) != 1:
        raise ValueError("课程别名不存在或不唯一，请使用课程 ID")
    return resource_id(matches[0])


def emit_resource(data, output: Path | None = None):
    if output:
        write_json(output.expanduser().resolve(), data)
    if json_mode:
        emit(data)
    elif isinstance(data, list):
        keys = ("id", "name", "title", "display_name", "due_at", "begin", "end", "type", "size")
        emit([{k: row[k] for k in keys if k in row} for row in data])
    else:
        console.print_json(data=data, ensure_ascii=False)
    if output and not json_mode:
        console.print(f"已保存 JSON：{output.expanduser().resolve()}", markup=False)


register(app, get_client=lambda: client(), course_id=course_id, emit=emit_resource, guarded=guarded)
register_videos(
    app,
    get_client=lambda: client(),
    course_id=course_id,
    emit=emit_resource,
    guarded=guarded,
    is_json=lambda: json_mode,
    console=console,
)


def resolve(course: str, output: Path | None = None):
    cfg = settings()
    matches = [
        (cid, item) for cid, item in cfg["courses"].items() if course == cid or course == item.get("name")
    ]
    if len(matches) > 1:
        raise ValueError("课程别名不唯一，请使用课程 ID")
    if matches:
        cid, item = matches[0]
        return cid, (output or Path(item["path"])).expanduser().resolve()
    if not course.isdigit():
        raise ValueError("未找到课程别名；运行 canvas courses 查看 ID，再用 canvas bind 关联")
    if output is None:
        raise ValueError("课程尚未关联目录；请先 canvas bind，或传入 --output")
    return course, output.expanduser().resolve()


@app.command()
@guarded
def login(
    browser: bool = typer.Option(False, "--browser", help="在独立 Edge 窗口手动登录并保存 Canvas 会话"),
    channel: str = typer.Option("msedge", help="浏览器通道：msedge / chrome"),
    token_stdin: bool = typer.Option(False, "--token-stdin", help="从标准输入读取 Token，避免命令行历史"),
):
    """登录并验证身份；Windows 凭据由 DPAPI 加密。"""
    base = settings()["base_url"]
    if browser and token_stdin:
        raise ValueError("--browser 和 --token-stdin 不能同时使用")
    if browser:
        if channel not in {"msedge", "chrome"}:
            raise ValueError("浏览器通道应为 msedge 或 chrome")
        auth, profile = browser_login(base, channel)
    else:
        if not token_stdin and not sys.stdin.isatty():
            raise ValueError("请在终端运行 canvas login，或显式使用 --token-stdin")
        if not token_stdin:
            console.print("Canvas → 账户 → 设置 → 新建访问许可证。Token 只在本机输入。")
        token = (
            sys.stdin.readline().strip()
            if token_stdin
            else getpass.getpass("Canvas Token（隐藏输入）: ").strip()
        )
        if not token:
            raise ValueError("Token 不能为空")
        auth = {"base_url": base, "token": token}
        with CanvasClient(base, auth) as c:
            profile = c.get("/api/v1/users/self/profile")
        save_auth(auth)
    emit(
        {"authenticated": True, "name": profile.get("name"), "method": "browser" if browser else "token"},
        f"已登录：{profile.get('name', 'Canvas 用户')}。凭据已加密保存在本机。",
    )


@app.command()
@guarded
def logout():
    """移除本工具保存的凭据（不会撤销 Canvas 上的 Token）。"""
    (home() / "auth.dpapi").unlink(missing_ok=True)
    emit({"removed": True}, "已移除本地凭据。CANVAS_TOKEN 环境变量如有设置，仍会生效。")


@app.command()
@guarded
def courses(
    course: str | None = typer.Argument(None, help="可选：课程 ID 或已设置的别名"),
    output: Path | None = typer.Option(None, "--output", "-o"),
):
    """列出活跃课程，或读取指定课程详情；JSON 保留原始 API 字段。"""
    with client() as c:
        rows = c.get(f"/api/v1/courses/{course_id(course)}") if course else c.courses()
    emit_resource(rows, output)


@app.command()
@guarded
def bind(course_id: str, path: Path, name: str = typer.Option("", help="简短别名，例如 数集")):
    """将课程 ID 关联到资料目录；不改动已有文件。"""
    if not course_id.isdigit():
        raise ValueError("请输入数字课程 ID")
    cfg = settings()
    if name and any(c != course_id and i.get("name") == name for c, i in cfg["courses"].items()):
        raise ValueError("这个课程别名已被使用")
    cfg["courses"][course_id] = {"name": name or course_id, "path": str(path.expanduser().resolve())}
    save_settings(cfg)
    emit(cfg["courses"][course_id], f"已关联 {name or course_id} → {path.expanduser().resolve()}")


@app.command()
@guarded
def sync(
    course: str,
    output: Path | None = typer.Option(None, "--output", "-o"),
    dry_run: bool = typer.Option(False, help="只查看待下载项，不写入课程目录"),
    all_files: bool = typer.Option(False, help="包含压缩包、视频和安装程序等全部文件"),
    review: bool = typer.Option(False, help="显式启用额外的 Markdown 复习导出"),
    questions: bool = typer.Option(False, help="在快照中额外抓取测验题目"),
):
    """同步单元课件和作业附件；默认跳过视频、压缩包、安装程序。"""
    cid, directory = resolve(course, output)
    with client() as c:
        if not json_mode:
            console.print("正在读取课程资料…", style="cyan")
        catalog = discover(c, cid, questions=(questions or review) and not dry_run)

        def progress(item):
            if not json_mode:
                labels = {
                    "downloaded": "已下载",
                    "unchanged": "已校验",
                    "pending": "待下载",
                    "excluded": "已跳过",
                    "locked": "未开放",
                    "failed": "失败",
                }
                console.print(f"{labels[item['status']]:<5} {item['name']}", markup=False)

        if dry_run:
            results = sync_files(c, catalog, directory, dry_run=True, all_files=all_files, progress=progress)
            review_result = None
        else:
            with CourseLock(directory):
                write_json(directory / ".canvas" / "catalog.json", catalog)
                results = sync_files(c, catalog, directory, all_files=all_files, progress=progress)
                write_json(directory / ".canvas" / "last-run.json", results)
                review_result = None
                if review:
                    review_result = review_exporter()(catalog, directory)
    counts = dict(Counter(r["status"] for r in results))
    partial = bool(
        catalog["warnings"]
        or counts.get("failed")
        or counts.get("locked")
        or (review_result and review_result["issues"])
    )
    data = {
        "course_id": cid,
        "output": str(directory),
        "dry_run": dry_run,
        "counts": counts,
        "files": results,
        "review": review_result,
        "warnings": catalog["warnings"],
        "partial": partial,
    }
    emit(
        data,
        f"{'预览' if dry_run else '同步'}完成：{counts}\n目录：{directory}"
        + (f"\n题集：{review_result['path']}" if review_result else "")
        + ("\n有未完成项目，请查看 JSON warnings 和 .canvas/last-run.json。" if partial else ""),
    )
    if partial:
        raise typer.Exit(3)


@app.command(hidden=True)
@guarded
def review(
    course: str,
    output: Path | None = typer.Option(None, "--output", "-o", help="课程资料目录"),
    html_dir: Path | None = typer.Option(None, help="导入保存的测验结果 HTML，文件名为作业ID.html"),
):
    """从本地同步快照离线生成题集，无需重复联网。"""
    generate_review = review_exporter()
    cid, directory = resolve(course, output)
    catalog = read_json(directory / ".canvas" / "catalog.json")
    if not catalog:
        raise ValueError("还没有本地快照，请先运行 canvas sync")
    if str(catalog["course_id"]) != cid:
        raise ValueError("本地快照的课程 ID 不匹配")
    with CourseLock(directory):
        result = generate_review(catalog, directory, html_dir=html_dir)
    emit(
        result,
        f"已生成：{result['path']}\n{result['assignments']} 项作业，{result['questions']} 道题，"
        f"{len(result['issues'])} 条待核对记录。",
    )
    if result["issues"]:
        raise typer.Exit(3)


export_app = typer.Typer(no_args_is_help=True, help="可选的内容转换与导出，独立于资源获取。")
export_app.command("review")(review)
app.add_typer(export_app, name="export")


@app.command()
@guarded
def status():
    """查看课程绑定和上次同步结果（离线）。"""
    rows = []
    for cid, item in settings()["courses"].items():
        catalog = read_json(Path(item["path"]) / ".canvas" / "catalog.json", {})
        rows.append(
            {
                "ID": cid,
                "别名": item["name"],
                "目录": item["path"],
                "上次采集": catalog.get("fetched_at", "尚未同步"),
            }
        )
    emit(rows)


@app.command()
@guarded
def doctor(online: bool = typer.Option(False, help="额外验证 Canvas 身份和网络")):
    """检查配置、运行环境和登录状态；不输出凭据。"""
    checks = {
        "version": __version__,
        "python": sys.version.split()[0],
        "config_home": str(home()),
        "credential_present": bool(os.environ.get("CANVAS_TOKEN") or (home() / "auth.dpapi").exists()),
        "browser_login_installed": importlib.util.find_spec("playwright") is not None,
        "bound_courses": len(settings()["courses"]),
    }
    if online:
        with client() as c:
            checks["authenticated"] = bool(c.get("/api/v1/users/self/profile").get("id"))
    emit(checks)


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        app()
    except KeyboardInterrupt:
        console.print("已停止。已完成文件保留，下次 sync 会继续。", markup=False)
        raise SystemExit(130) from None
