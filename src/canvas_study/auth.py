from __future__ import annotations

import ctypes
import json
import os
import sys
from urllib.parse import urlsplit

from .storage import atomic_write, home


def _dpapi(data: bytes, decrypt: bool = False) -> bytes:
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source, result = Blob(len(data), buffer), Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    fn = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    fn.argtypes = [
        ctypes.POINTER(Blob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(Blob),
    ]
    fn.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise ValueError("Windows 凭据加密服务不可用")
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel.LocalFree(result.data)


def save_auth(value: dict) -> None:
    if os.name != "nt":
        raise ValueError("此版本的持久凭据库使用 Windows DPAPI；其他系统请用 CANVAS_TOKEN 环境变量")
    atomic_write(home() / "auth.dpapi", _dpapi(json.dumps(value).encode()))


def load_auth(base_url: str) -> dict:
    if token := os.environ.get("CANVAS_TOKEN", "").strip():
        return {"token": token}
    path = home() / "auth.dpapi"
    if not path.exists():
        raise ValueError("尚未登录。运行 canvas login，或 canvas login --browser")
    if os.name != "nt":
        raise ValueError("无法在其他系统解密 Windows 凭据，请设置 CANVAS_TOKEN")
    try:
        value = json.loads(_dpapi(path.read_bytes(), decrypt=True))
    except (ValueError, OSError):
        raise ValueError("无法解密本地凭据，请重新运行 canvas login") from None
    if value.get("base_url") != base_url:
        raise ValueError("凭据所属站点不匹配，请重新登录")
    return value


def browser_login(base_url: str, channel: str = "msedge") -> tuple[dict, dict]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise ValueError("浏览器登录需要可选依赖：uv sync --extra browser") from None
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=channel, headless=False)
        try:
            context = browser.new_context()
            page = context.new_page()
            page.goto(base_url + "/login", wait_until="domcontentloaded", timeout=60000)
            print("请在新窗口完成 jAccount 登录；完成后回到此终端按 Enter。", file=sys.stderr)
            input()
            # Confirm authenticated API access; a redirected URL alone is insufficient.
            response = context.request.get(base_url + "/api/v1/users/self/profile", timeout=30000)
            if not response.ok or "json" not in response.headers.get("content-type", ""):
                raise ValueError("未检测到有效 Canvas 会话，请完成登录后重试")
            profile = response.json()
            if not isinstance(profile, dict) or not profile.get("id"):
                raise ValueError("Canvas 未返回有效身份，请完成登录后重试")
            host = urlsplit(base_url).hostname
            cookies = [
                c
                for c in context.cookies(base_url)
                if host == c["domain"].lstrip(".") or host.endswith("." + c["domain"].lstrip("."))
            ]
            auth = {"base_url": base_url, "cookies": cookies}
            save_auth(auth)
            return auth, profile
        finally:
            browser.close()
