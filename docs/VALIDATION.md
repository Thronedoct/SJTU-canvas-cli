# 验证记录

## 0.3 课堂视频与文字稿（2026-11-03）

57 项自动测试通过，Ruff 检查通过；0.3.0 wheel / sdist 构建成功。新增测试覆盖 Cookie 与 Token 两种 LTI 路径、认证范围隔离、非法跳转、分页中断和重复页、课程内录像归属、暂无文字稿/投影数据、时间戳处理、日期筛选、批量重复执行与文件冲突。

已通过现有真实 Cookie 会话完成：

- 课程 93742 列出 26 节课堂录像，入口工具自动发现。
- 录像 133716 获取 1,420 个语音片段、237 个整理段落，partial=false。
- 录像 133953 导出 JSON、TXT、SRT、VTT 四种格式，文件均实际保存。
- 对这节文字稿检索“电路”，返回 13 条带时间戳的匹配段落，partial=false。
- 录像 133716 投影接口返回 48 条 docList 记录；保留平台 OCR/图片元数据。
- 录像 215075 暂无文字稿，正确返回 unavailable、partial=true 与退出码 3。

未逐节验证全部录像，也未下载完整视频。真实 Token 视频启动、其他课程/学校、字幕与音频逐秒对齐尚未验证。语音文本是平台已有 ASR 结果，未经过人工校对。验证产物仅在被忽略的 .local 目录中，不进入安装包。

## 实际登录与只读接口验证

已完成真实 Canvas 浏览器登录，会话使用 Windows DPAPI 加密保存在项目本地配置目录。CLI 的在线身份检查、课程列表、单个作业详情均已成功读取。未在文档中记录凭据或个人身份信息。尚未据此宣称所有下载和测验题型都已完成真实测试。

修复了 HTTPX 对 NO_PROXY 中带方括号 IPv6 地址的兼容问题；保留原有代理设置，临时规范化后恢复进程环境。浏览器登录验证成功即保存会话，不再重复通过 HTTPX 验证后才保存。相关回归测试加入后，共 38 项测试通过。


## 0.2 通用资源接口

35 项测试通过，Ruff 检查通过。新增验证包括：

- 不绑定目录也能读取任意课程 ID 下的作业；获取操作不创建本地文件。
- 原始 HTML、rubric、未知附加字段、复杂题目选项字段完整返回。
- fetch 默认不请求测验题目或提交记录；输出结构独立于文档转换。
- 列表分页及原始 JSON 文件保存。
- sync 默认不读测验题目、不生成 Markdown。
- 单元条目、页面、文件详情、本人提交记录请求路径正确，全部使用 GET。
- 缺少 pypdf / markdownify 时仍可导入核心 CLI。

以上为 0.2 发布时的离线验证；后续真实登录和视频验证见文首。

## 0.1 基线记录

日期：2026-11-03。Windows / Python 3.12.4。

## 已验证

- 独立虚拟环境安装完成，`uv.lock` 固定依赖解析结果。
- `canvas.cmd --help`、`--version`、`doctor`、`bind`、`status` 已在本机执行。
- 数集课程 ID 93742 已绑定指定目录；绑定本身未改动资料目录。
- 28 项自动测试通过，Ruff 检查通过。API 测试使用 httpx.MockTransport，不调用真实课程。
- 端到端模拟：目录发现 → 下载 → 再次同步不重复下载 → 生成 Markdown。
- 分页、限流重试、登录跳转检测、跨站凭据隔离。
- Range 续传、服务器忽略 Range 的完整重下、错误范围 / 文件大小 / HTML 登录页检测。
- SHA-256 变动检测；本地改动文件不会自动覆盖；路径穿越与 Windows 保留文件名处理。
- Windows 进程锁与 DPAPI 加密/解密。加密测试使用假 Token，验证文件不含明文 Token。
- 已完成测验尝试选择、题目 API 拒绝时结果页回退、未完成测验不触发开始。
- Canvas LaTeX 公式、图片相对路径、保存的 HTML 导入、缺失提示、CLI JSON 与退出码。
- 人工示例已实际生成并检查，位于 `demo-output/复习/作业题目汇总.md`。

## 0.1 发布时尚未验证（历史记录）

- 没有向 CLI 提供用户真实 Token 或 Cookie，因此没有完成真实账号的端到端下载。
- 没有宣称已下载课程 93742 的资料或完整整理所有真实作业。原目录已有的 3 份课件不算本工具的下载成果。
- 浏览器手动登录代码已实现；尚未在真实 jAccount 登录过程中验证。
- 大型 PDF、扫描件、复杂题型及 New Quizzes 需进一步真实样本验证。

## 受限环境运行测试

该环境的默认临时目录无法供 pytest 创建测试目录，改用项目内每次新建的测试目录：

```powershell
New-Item -ItemType Directory -Force .local\tests | Out-Null
$testRun = Join-Path (Get-Location) ('.local\tests\' + [guid]::NewGuid().ToString())
.venv\Scripts\python.exe -m pytest -q --tb=short --basetemp $testRun
.venv\Scripts\ruff.exe check src tests
```

0.3 当前结果：`57 passed` / `All checks passed!`。普通开发环境可直接 `uv run pytest -q`。
