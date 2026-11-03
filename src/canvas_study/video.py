"""SJTU video resources through a course-scoped Canvas LTI session.

Protocol references: Canvas external_tools API and CanvasKit's documented
SJTU endpoint discovery. Independent implementation; never stores LTI/JWT tokens.
"""

from __future__ import annotations

import time
from urllib.parse import parse_qs, urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

from .client import CanvasError, make_http_client, origin
from .resources import resource_id

VIDEO_ORIGIN = "https://v.sjtu.edu.cn"
API_ROOT = VIDEO_ORIGIN + "/jy-application-resourcemanage"
VIDEO_UI = VIDEO_ORIGIN + "/jy-application-resourcemanage-ui/"


def checked_url(url: str, canvas_base: str) -> str:
    parsed = urlsplit(url)
    allowed = {origin(canvas_base), origin(VIDEO_ORIGIN)}
    if parsed.scheme != "https" or parsed.username or parsed.password or origin(url) not in allowed:
        raise CanvasError("视频登录跳转到未支持的站点，已停止；未发送认证数据")
    return url


def token_from_url(url: str) -> str | None:
    parsed = urlsplit(url)
    for part in (parsed.query, parsed.fragment.partition("?")[2] or parsed.fragment):
        value = parse_qs(part).get("jwt_token", [])
        if value:
            return value[0]
    return None


def login_form(text: str, response_url: str, canvas_base: str):
    soup = BeautifulSoup(text, "html.parser")
    for form in soup.select("form"):
        fields = {
            str(node["name"]): str(node.get("value", ""))
            for node in form.select("input[name]")
            if node.get("type", "").lower() in {"hidden", ""}
        }
        if not ({"id_token", "login_hint", "lti_message_hint"} & fields.keys()):
            continue
        method = form.get("method", "GET").upper()
        if method not in {"GET", "POST"}:
            raise CanvasError("不支持的视频认证表单方法")
        action = checked_url(urljoin(response_url, form.get("action", "")), canvas_base)
        path = urlsplit(action).path.lower()
        if not any(marker in path for marker in ("lti", "oidc", "authorize", "launch")):
            raise CanvasError("视频认证表单目标不是预期的 LTI 接口")
        return method, action, fields
    raise CanvasError("视频登录未返回 LTI 认证表单；请重新登录或检查课程视频权限")


def api_data(payload):
    if not isinstance(payload, dict):
        raise CanvasError("课堂视频接口响应格式发生变化")
    if payload.get("ok") is False or payload.get("success") is False:
        # Do not echo bodies: they may contain identifiers or authentication context.
        raise CanvasError("课堂视频平台拒绝了请求；检查课程权限或重新登录")
    if payload.get("dataOk") is False and payload.get("data") is not None:
        raise CanvasError("课堂视频平台未返回可用数据")
    if "data" not in payload:
        raise CanvasError("课堂视频接口缺少 data 字段")
    return payload["data"]


class VideoClient:
    def __init__(self, canvas, course: str, *, tool_id: str | None = None, transport=None, sleep=time.sleep):
        if origin(canvas.base) != origin("https://oc.sjtu.edu.cn"):
            raise CanvasError("当前课堂视频适配器仅支持 SJTU Canvas，通用 Canvas 资源命令不受影响")
        self.canvas = canvas
        self.course = resource_id(course)
        self.tool_id = resource_id(tool_id) if tool_id else None
        self.http = make_http_client(
            transport=transport,
            timeout=httpx.Timeout(45, connect=15),
            follow_redirects=False,
            headers={"User-Agent": "CanvasCLI/0.3"},
        )
        for cookie in canvas.auth.get("cookies", []):
            if cookie.get("expires", -1) <= 0 or cookie["expires"] > time.time():
                self.http.cookies.set(
                    cookie["name"],
                    cookie["value"],
                    domain=urlsplit(canvas.base).hostname,
                    path=cookie.get("path", "/"),
                )
        self.sleep = sleep
        self._jwt = None
        self._class_id = None
        self._records = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.http.close()
        self._jwt = None

    def tools(self) -> list:
        return self.canvas.list(f"/api/v1/courses/{self.course}/external_tools", {"include_parents": "true"})

    def choose_tool(self) -> str:
        if self.tool_id:
            return self.tool_id
        candidates = [
            t
            for t in self.tools()
            if urlsplit(t.get("url") or "").hostname == "v.sjtu.edu.cn"
            and ("课堂视频" in t.get("name", "") or "lecture" in t.get("name", "").lower())
        ]
        if len(candidates) != 1:
            raise CanvasError("未找到唯一的课堂视频入口；运行 videos tools 查看，再通过 --tool-id 指定")
        self.tool_id = str(candidates[0]["id"])
        return self.tool_id

    def launch(self) -> str:
        tool_id = self.choose_tool()
        if self.canvas.auth.get("cookies"):
            response = self.canvas.request(f"/courses/{self.course}/external_tools/{tool_id}")
            try:
                method, url, fields = login_form(response.text, str(response.url), self.canvas.base)
            finally:
                response.close()
        else:
            launch = self.canvas.get(
                f"/api/v1/courses/{self.course}/external_tools/sessionless_launch", {"id": tool_id}
            )
            if not isinstance(launch, dict) or not launch.get("url"):
                raise CanvasError("Canvas 未返回有效的视频启动地址")
            url = checked_url(launch["url"], self.canvas.base)
            method, fields = "GET", None
        for _ in range(16):
            if origin(url) == origin(VIDEO_ORIGIN) and (token := token_from_url(url)):
                return token
            try:
                response = self.http.request(
                    method,
                    url,
                    params=fields if method == "GET" else None,
                    data=fields if method == "POST" else None,
                )
            except httpx.TransportError:
                raise CanvasError("视频认证网络连接失败；检查校园网络或 VPN 后重试") from None
            try:
                if response.is_redirect:
                    if not response.headers.get("Location"):
                        raise CanvasError("视频认证重定向缺少目标地址")
                    next_url = checked_url(
                        urljoin(str(response.url), response.headers["Location"]), self.canvas.base
                    )
                    if response.status_code in {301, 302, 303}:
                        method, fields = "GET", None
                    url = next_url
                elif response.status_code >= 400:
                    raise CanvasError(f"视频认证失败（HTTP {response.status_code}）", response.status_code)
                else:
                    method, url, fields = login_form(response.text, str(response.url), self.canvas.base)
            finally:
                response.close()
        raise CanvasError("课堂视频认证重定向次数过多")

    def get(self, path: str, params=None):
        if not path.startswith("/") or path.startswith("//") or "?" in path:
            raise ValueError("视频 API 路径不合法")
        if self._jwt is None:
            self._jwt = self.launch()
        refreshed = False
        for attempt in range(4):
            try:
                response = self.http.get(
                    API_ROOT + path, params=params, headers={"jwt-token": self._jwt, "Referer": VIDEO_UI}
                )
            except httpx.TransportError:
                if attempt == 3:
                    raise CanvasError("课堂视频接口连接失败；检查校园网络或 VPN") from None
                self.sleep(2**attempt)
                continue
            try:
                if response.status_code == 401 and not refreshed:
                    self._jwt = self.launch()
                    refreshed = True
                    continue
                if response.status_code in {429, 500, 502, 503, 504} and attempt < 3:
                    retry = response.headers.get("Retry-After", "")
                    delay = min(30, int(retry)) if retry.isdigit() else 2**attempt
                    self.sleep(delay)
                    continue
                if response.is_redirect:
                    raise CanvasError("视频 API 返回跳转，已停止以避免向其他站点发送视频凭据")
                if response.status_code >= 400:
                    raise CanvasError(f"课堂视频接口返回 HTTP {response.status_code}", response.status_code)
                try:
                    return api_data(response.json())
                except ValueError:
                    raise CanvasError("课堂视频接口没有返回 JSON") from None
            finally:
                response.close()
        raise CanvasError("课堂视频请求重试次数已用尽")

    def teaching_class(self) -> str:
        if self._class_id is None:
            data = self.get("/lms/launch-context")
            try:
                self._class_id = resource_id(str(data["canvasRecord"]["teachingClassId"]))
            except (KeyError, TypeError, ValueError):
                raise CanvasError("当前 Canvas 课程没有关联的视频教学班") from None
        return self._class_id

    def records(self) -> list:
        if self._records is not None:
            return self._records
        rows, seen = [], set()
        for page in range(1, 201):
            data = self.get(
                "/v1/subject_vod_list_new",
                {
                    "page.pageIndex": page,
                    "page.pageSize": 100,
                    "teclIds": self.teaching_class(),
                    "page.orders[0].asc": "false",
                    "page.orders[0].field": "courBeginTime",
                    "schoolOpenStatusFlag": "false",
                },
            )
            if not isinstance(data, dict) or not isinstance(data.get("records"), list):
                raise CanvasError("课堂视频列表格式发生变化")
            batch = data["records"]
            fresh = [r for r in batch if str(r.get("id")) not in seen]
            if batch and not fresh:
                raise CanvasError("视频接口返回重复分页，未将列表当作完整结果")
            for row in fresh:
                resource_id(str(row.get("id")))
                seen.add(str(row["id"]))
                rows.append(row)
            total = data.get("total") or data.get("totalCount")
            if not batch and total is not None and len(rows) < int(total):
                raise CanvasError("课堂视频列表在达到总数前中断，未将结果标记为完整")
            if (total is not None and len(rows) >= int(total)) or not batch:
                self._records = rows
                return rows
            if len(batch) < 100 and total is None:
                self._records = rows
                return rows
        raise CanvasError("视频列表超过分页上限，请缩小范围或检查接口")

    def record(self, recording_id: str) -> dict:
        recording_id = resource_id(recording_id)
        for row in self.records():
            if str(row["id"]) == recording_id:
                return row
        raise CanvasError("该课堂视频不在当前课程可访问的列表中", 404)

    def transcript(self, recording_id: str) -> dict:
        from .transcripts import normalize_transcript

        record = self.record(recording_id)
        data = self.get(f"/v1/course/ai/translate/{resource_id(recording_id)}", {"useOriginal": "true"})
        return normalize_transcript(data, course_id=self.course, record=record)

    def slides(self, recording_id: str) -> dict:
        self.record(recording_id)
        data = self.get("/v1/course/ai/ppt", {"courseId": resource_id(recording_id)})
        if data is not None and not isinstance(data, dict):
            raise CanvasError("投影截图接口格式发生变化")
        available = bool(data and data.get("docList"))
        return {
            "course_id": self.course,
            "recording_id": str(recording_id),
            "source": "sjtu_video",
            "data": data,
            "status": "available" if available else "unavailable",
            "partial": not available,
            "warnings": [] if available else ["平台暂无可访问的投影截图/OCR 数据"],
        }
