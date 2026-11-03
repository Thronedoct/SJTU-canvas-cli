from __future__ import annotations

import hashlib
import re
from pathlib import Path

import httpx

from .client import CanvasError
from .storage import read_json, safe_name, sha256, under, write_json

MATERIAL_EXTENSIONS = {
    ".pdf",
    ".ppt",
    ".pptx",
    ".doc",
    ".docx",
    ".md",
    ".txt",
    ".html",
    ".htm",
    ".ipynb",
    ".csv",
    ".xlsx",
    ".lib",
    ".pm",
    ".sp",
    ".cir",
}


def fingerprint(file: dict) -> str:
    return hashlib.sha256(f"{file['id']}:{file.get('updated_at')}:{file.get('size')}".encode()).hexdigest()[
        :10
    ]


def destination(file: dict, *, versioned: bool = True) -> str:
    source = file["sources"][0]
    category = "作业附件" if source in {"作业附件", "题目图片"} else "课件/" + safe_name(source, 65)
    name = safe_name(file["name"])
    path = Path(name)
    # Stable ID + revision prevents collisions and preserves previous versions.
    name = (
        f"{path.stem}__{file['id']}_{fingerprint(file)}{path.suffix}"
        if versioned
        else path.stem.rstrip() + path.suffix
    )
    return f"{category}/{name}"


def selected(file, all_files=False):
    return (
        all_files
        or Path(file["name"]).suffix.lower() in MATERIAL_EXTENSIONS
        or any(s in {"作业附件", "题目图片"} for s in file["sources"])
    )


def verified(root: Path, entry: dict, file: dict) -> bool:
    if entry.get("fingerprint") != fingerprint(file) or not entry.get("sha256"):
        return False
    path = under(root, entry["path"])
    return path.is_file() and path.stat().st_size == entry["size"] and sha256(path) == entry["sha256"]


def download_one(client, file: dict, target: Path) -> dict:
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    meta_path = target.with_name(target.name + ".part.json")
    meta = read_json(meta_path, {})
    validator = meta.get("validator") if meta.get("fingerprint") == fingerprint(file) else None
    offset = part.stat().st_size if part.exists() and validator else 0
    # Refresh signed URL at the moment of download; never persist it in manifests.
    remote = client.get(f"/api/v1/files/{file['id']}")
    if remote.get("updated_at", "") != file.get("updated_at", "") or remote.get("size") != file.get("size"):
        raise CanvasError("文件在同步期间发生更新，请重新同步")
    if not remote.get("url"):
        raise CanvasError("文件没有可用下载地址")
    headers = {"Range": f"bytes={offset}-", "If-Range": validator} if offset else {}
    try:
        response = client.request(remote["url"], stream=True, headers=headers, api=False)
    except CanvasError as error:
        if error.status != 416 or not offset:
            raise
        offset = 0
        response = client.request(remote["url"], stream=True, api=False)
    try:
        content_type = response.headers.get("Content-Type", "").lower()
        if "text/html" in content_type and target.suffix.lower() not in {".html", ".htm"}:
            raise CanvasError("下载返回了 HTML 页面，未将其保存为课件")
        append = response.status_code == 206 and offset > 0
        if response.status_code == 206:
            match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+|\*)", response.headers.get("Content-Range", ""))
            if not match or int(match[1]) != offset:
                raise CanvasError("服务器返回的断点范围不正确，保留临时文件以便检查")
        etag = response.headers.get("ETag", "")
        validator = etag if etag and not etag.startswith("W/") else response.headers.get("Last-Modified")
        write_json(meta_path, {"fingerprint": fingerprint(file), "validator": validator})
        try:
            with part.open("ab" if append else "wb") as f:
                for chunk in response.iter_bytes(256 * 1024):
                    f.write(chunk)
        except httpx.TransportError:
            raise CanvasError("下载中断，已保留临时文件；重新 sync 可继续") from None
    finally:
        response.close()
    if file.get("size") is not None and part.stat().st_size != file["size"]:
        raise CanvasError("下载大小与 Canvas 记录不一致，未标记完成")
    digest = sha256(part)
    if target.exists():
        raise CanvasError("目标路径已有文件，未覆盖；请检查后重试")
    part.replace(target)
    meta_path.unlink(missing_ok=True)
    return {"size": target.stat().st_size, "sha256": digest, "fingerprint": fingerprint(file)}


def sync_files(client, catalog: dict, root: Path, *, dry_run=False, all_files=False, progress=None):
    state_path = root / ".canvas" / "manifest.json"
    state = read_json(state_path, {"schema_version": 1, "files": {}})
    results = []
    for file in catalog["files"]:
        previous = state["files"].get(file["id"], {})
        preferred_path = state.get("path_overrides", {}).get(file["id"]) or destination(
            file, versioned=state.get("filename_policy") != "plain"
        )
        under(root, preferred_path)  # Validate custom paths even for dry-run results.
        item = {"id": file["id"], "name": file["name"], "path": preferred_path}
        if not selected(file, all_files):
            item["status"] = "excluded"
        elif file["locked"]:
            item["status"] = "locked"
        elif verified(root, previous, file):
            item.update(previous, status="unchanged")
        elif dry_run:
            item["status"] = "pending"
        else:
            target = under(root, item["path"])
            try:
                if target.exists():
                    raise CanvasError("本地文件未通过校验或不在清单中，未覆盖；请移走后重试")
                item.update(download_one(client, file, target), status="downloaded")
                state["files"][file["id"]] = item.copy()
                write_json(state_path, state)
            except (CanvasError, OSError) as error:
                item.update(status="failed", error=str(error))
        results.append(item)
        if progress:
            progress(item)
    return results
