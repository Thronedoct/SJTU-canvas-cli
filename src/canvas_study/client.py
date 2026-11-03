from __future__ import annotations

import os
import re
import threading
from urllib.request import getproxies
import time
from urllib.parse import urljoin, urlsplit

import httpx


_proxy_environment_lock = threading.RLock()


def normalize_no_proxy(value: str) -> str:
    # HTTPX 0.28 misclassifies bracketed IPv6 bypass entries as host wildcards.
    # Explicit URL patterns preserve both the IPv6 address and optional port.
    return ",".join(
        "all://" + item.strip()
        if re.fullmatch(r"\[[0-9a-fA-F:%.]+\](?::[0-9]+)?", item.strip())
        else item.strip()
        for item in value.split(",")
    )


def make_http_client(**kwargs):
    # Keep environment/Windows proxy and certificate settings intact. The override
    # is process-local, only during construction, and is always restored.
    with _proxy_environment_lock:
        original_bypass = getproxies().get("no", "")
        normalized = normalize_no_proxy(original_bypass)
        if normalized == original_bypass:
            return httpx.Client(**kwargs)
        previous = os.environ.get("no_proxy")
        os.environ["no_proxy"] = normalized
        try:
            return httpx.Client(**kwargs)
        finally:
            if previous is None:
                os.environ.pop("no_proxy", None)
            else:
                os.environ["no_proxy"] = previous


class CanvasError(Exception):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


def origin(url: str) -> tuple:
    p = urlsplit(url)
    return p.scheme, p.hostname, p.port or (443 if p.scheme == "https" else 80)


class CanvasClient:
    def __init__(self, base_url: str, auth: dict, *, transport=None, sleep=time.sleep):
        self.base = base_url.rstrip("/")
        if urlsplit(self.base).scheme != "https" or urlsplit(self.base).username:
            raise ValueError("Canvas 地址必须使用 HTTPS")
        self.auth = auth
        self.sleep = sleep
        self.http = make_http_client(
            transport=transport,
            timeout=httpx.Timeout(45, connect=15),
            follow_redirects=False,
            headers={"User-Agent": "CanvasStudy/0.1"},
        )

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.http.close()

    def request(self, path: str, *, params=None, stream=False, headers=None, api=True):
        url = urljoin(self.base + "/", path)
        if api and origin(url) != origin(self.base):
            raise CanvasError("API 分页指向了其他站点，已停止")
        for _redirect in range(8):
            parts = urlsplit(url)
            if parts.scheme != "https" or parts.username or parts.password:
                raise CanvasError("拒绝非 HTTPS 下载地址")
            if any(x in parts.path.lower() for x in ("/login", "/signin")):
                raise CanvasError("登录已过期，请运行 canvas login", 401)
            request_headers = {"Accept-Encoding": "identity", **(headers or {})}
            # Explicit scope on every hop: no Canvas token/cookie is sent to file CDNs.
            if origin(url) == origin(self.base):
                if self.auth.get("token"):
                    request_headers["Authorization"] = "Bearer " + self.auth["token"]
                cookies = [
                    c
                    for c in self.auth.get("cookies", [])
                    if (c.get("expires", -1) <= 0 or c["expires"] > time.time())
                    and (
                        parts.path == c.get("path", "/")
                        or parts.path.startswith(c.get("path", "/").rstrip("/") + "/")
                    )
                ]
                if cookies:
                    request_headers["Cookie"] = "; ".join(c["name"] + "=" + c["value"] for c in cookies)
            for attempt in range(4):
                try:
                    self.http.cookies.clear()
                    request = self.http.build_request("GET", url, params=params, headers=request_headers)
                    response = self.http.send(request, stream=stream)
                except httpx.TransportError:
                    if attempt == 3:
                        raise CanvasError("网络连接失败，请检查网络或校园 VPN 后重试") from None
                    self.sleep(2**attempt)
                    continue
                if response.status_code not in {429, 500, 502, 503, 504} or attempt == 3:
                    break
                delay = response.headers.get("Retry-After", "")
                response.close()
                self.sleep(min(30, float(delay)) if delay.isdigit() else 2**attempt)
            if response.is_redirect:
                next_url = urljoin(str(response.url), response.headers.get("Location", ""))
                response.close()
                if api and origin(next_url) != origin(self.base):
                    raise CanvasError("API 请求跳转至登录或其他站点，请重新登录", 401)
                url, params = next_url, None
                continue
            if response.status_code >= 400:
                status = response.status_code
                response.close()
                message = {
                    401: "登录无效或已过期，请运行 canvas login",
                    403: "当前账号无权读取此资源",
                    404: "资源不存在或未开放",
                    429: "Canvas 暂时限流，请稍后重试",
                }.get(status, f"Canvas 返回 HTTP {status}")
                raise CanvasError(message, status)
            return response
        raise CanvasError("重定向次数过多，请重新登录")

    def get(self, path: str, params=None):
        r = self.request(path, params=params)
        try:
            return r.json()
        except ValueError:
            raise CanvasError("接口未返回 JSON，可能需要重新登录", 401) from None
        finally:
            r.close()

    def list(self, path: str, params=None, key=None) -> list:
        items, seen = [], set()
        params = {"per_page": 100, **(params or {})}
        while path:
            if path in seen:
                raise CanvasError("检测到循环分页链接")
            seen.add(path)
            r = self.request(path, params=params)
            try:
                payload = r.json()
                rows = payload.get(key, []) if key else payload
                if not isinstance(rows, list):
                    raise CanvasError("接口列表格式不符合预期")
                items.extend(rows)
                match = re.search(r'<([^>]+)>\s*;\s*rel="next"', r.headers.get("Link", ""))
                path = match.group(1) if match else None
                params = None
            except ValueError:
                raise CanvasError("接口未返回 JSON，可能需要重新登录", 401) from None
            finally:
                r.close()
        return items

    def courses(self):
        return self.list("/api/v1/courses", {"enrollment_state": "active"})

    def quiz_questions(self, course: str, quiz: str) -> list:
        base = f"/api/v1/courses/{course}/quizzes/{quiz}"
        # Match the student's completed attempt, especially for randomized quizzes.
        submissions = self.list(base + "/submissions", key="quiz_submissions")
        complete = [s for s in submissions if s.get("finished_at") and s.get("attempt")]
        if not complete:
            raise CanvasError("尚无可读取的已完成测验尝试；不会自动开始测验")
        chosen = max(complete, key=lambda s: s.get("attempt", 0))
        return self.list(
            base + "/questions",
            {"quiz_submission_id": chosen["id"], "quiz_submission_attempt": chosen["attempt"]},
        )
