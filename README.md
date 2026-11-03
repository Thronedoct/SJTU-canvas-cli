# SJTU Canvas CLI

通用的 Canvas 数据获取与文件操作工具，面向终端用户、脚本和 Agent。

核心能力是获取课程、作业、测验、单元、页面、文件，以及 SJTU 课堂视频的文字稿和投影 OCR。命令返回结构化数据，后续可用于检索、提醒、备份、学习笔记或其他应用。业务流程通过这些操作组合，工具不会默认把抓取结果加工成某种文档。

## 快速使用

首次使用先克隆项目并安装依赖（需要 Python 3.11+ 和 uv）：

```powershell
git clone https://github.com/Thronedoct/sjtu-canvas-cli.git
cd sjtu-canvas-cli
uv sync --extra browser
```

然后在项目目录的 PowerShell 中运行：

```powershell
# 首次认证（二选一）
.\canvas.cmd login
.\canvas.cmd login --browser

# 查询不需要绑定本地目录，直接使用任意可访问课程的 ID
.\canvas.cmd courses
.\canvas.cmd assignments list 93742
.\canvas.cmd assignments get 93742 428515

# 抓取作业 + 附件元数据，保存为 JSON
.\canvas.cmd --json assignments fetch 93742 428515 --output homework.json

# 显式请求题目或自己的提交记录
.\canvas.cmd --json assignments fetch 93742 426537 --questions --submission

# 下载选定的文件
.\canvas.cmd files download 13893010 --output HW1.pdf
```

以上 ID 只是当前课程的使用示例。源码没有固定课程、学科、学期、题目类型或保存目录。配置中的“数集”绑定属于当前用户的本地设置。

## 命令模型

| 资源 | 命令 | 返回内容 |
| --- | --- | --- |
| 课程 | `courses [COURSE]` | 活跃课程列表，或单个课程详情 |
| 作业 | `assignments list COURSE` | 作业列表，支持 `--search` |
| 作业 | `assignments get COURSE ID` | 原始作业对象：HTML 说明、时间、分值、提交类型、评分规则等 |
| 作业 | `assignments fetch COURSE ID` | 作业与附件元数据；可加 `--questions` / `--submission` |
| 作业附件 | `assignments attachments COURSE ID` | 说明中引用的 Canvas 文件元数据 |
| 我的提交 | `assignments submission COURSE ID` | 当前账号的提交状态、成绩等服务端返回信息 |
| 测验 | `quizzes list/get COURSE [ID]` | 列表或详情 |
| 题目 | `quizzes questions COURSE ID` | 已完成尝试的题目、选项及原始字段，注明来源与完整性 |
| 文件 | `files list COURSE` / `files get ID` | 文件列表或元数据 |
| 文件下载 | `files download ID --output PATH` | 下载单个文件并校验 |
| 单元 | `modules list/get COURSE [ID]` | 单元列表或详情 |
| 单元内容 | `modules items COURSE ID` | 单元中的文件、页面、作业等条目 |
| 页面 | `pages list COURSE` / `pages get COURSE SLUG` | 页面列表或 HTML 正文 |

资源查询命令支持 `--output PATH` 另存 JSON。`files download --output` 指文件保存路径。列表默认遍历全部分页，`--search` 按名称做本地筛选。

## 课堂视频与语音文字稿（0.3）

复用现有登录，自动发现课程的课堂视频入口；不需要手动提取视频 Token。先列出节次，再按视频 ID 获取：

```powershell
.\canvas.cmd videos list 93742
.\canvas.cmd videos list 93742 --since 2026-09-01 --until 2026-10-15

# 单节：原始数据、句子、段落与毫秒时间戳
.\canvas.cmd --json videos transcript 93742 133716 --output transcript.json
.\canvas.cmd videos transcript 93742 133716 --format txt --output lecture.txt

# 批量导出；日期边界包含当天，latest 取筛选后最近 N 节
.\canvas.cmd videos subtitles 93742 --until 2026-10-15 --output .\subtitles --formats json,txt,srt,vtt

# 查找关键词，返回所在节次、时间点和原文
.\canvas.cmd --json videos search 93742 电路 --until 2026-10-15

# 已有投影截图的时间点、链接与 OCR 原始数据
.\canvas.cmd --json videos slides 93742 133716 --output slides.json
```

语音文字稿来自平台已有的自动识别结果，可能存在错字。没有已发布文字稿时明确返回 `unavailable` 和退出码 `3`；不会自行启动新的语音转写。视频适配器目前支持 SJTU 新版课堂视频；普通 Canvas 资源命令仍独立可用。批量导出会跳过相同内容，覆盖不同内容须显式传 `--overwrite`。

[视频命令、数据格式与局限](docs/VIDEO.md)

## 数据契约

```powershell
.\canvas.cmd --json assignments get 93742 428515
```

stdout 输出 `{"ok":true,"data":...}`。`list/get` 的 `data` 保留 Canvas 原始字段和 HTML，保持 `id`、`description`、`due_at` 等 API 命名，不为某个应用裁剪字段。`--output` 文件仅保存 `data`，可直接由 Python / PowerShell / jq 读取。进度与人类提示写入 stderr。

`fetch` 返回 `{assignment, attachments, questions, submission, partial, warnings}`。未请求的可选项为 `null`。题目 API 成功时保留原始题目对象；使用可见结果 HTML 回退时，`source=result_html`，仅包含实际读到的字段，不伪造 API 中的评分数据。获取受账户权限和发布状态限制。

退出码：`0` 完成；`2` 参数、认证或网络等错误；`3` 已取得部分结果但有缺失；`130` 用户中断。部分成功仍输出取得的数据，调用方可检查 `partial` 与 `warnings`。

## 可选工作流

```powershell
# 只有希望同步到固定目录时，才需要 bind
.\canvas.cmd bind 93742 "D:\课程\数字集成电路" --name 数集
.\canvas.cmd sync 数集 --dry-run
.\canvas.cmd sync 数集

# 明确请求采集题目，保存为本地快照
.\canvas.cmd sync 数集 --questions

# 明确请求转换为 Markdown；这是一个可选消费者
.\canvas.cmd export review 数集
```

`sync` 默认归档课件和作业附件，不自动读取测验题目或生成复习文档。可用 `--review` 显式执行采集加导出的组合。旧版 `review` 入口仍兼容，新的文档入口为 `export review`。

[复习导出工作流与局限](docs/REVIEW-WORKFLOW.md) · [离线示例](examples/README.md)

## 安装与认证

需要 Python 3.11+ 和 uv。安装所需能力：

```powershell
uv sync                                      # 基础 CLI
uv sync --extra browser                       # 额外支持手动浏览器登录
uv sync --extra browser --extra export --group dev  # 本地开发与全部现有能力
```

核心不依赖 PDF 或 Markdown 转换库。资源层可直接供 Python 调用：`CanvasClient` 处理认证/分页/重试，`resources.py` 提供可复用的抓取操作；它们不创建本地目录或导出文档。

Token 在终端隐藏输入，来自 Canvas 账户设置。浏览器模式使用独立 Edge 窗口，完成登录后回终端按 Enter。Windows 使用 DPAPI 加密保存凭据；其他系统目前使用 `CANVAS_TOKEN` 环境变量。API Token / Cookie 仅发往配置的 Canvas 站点，文件跨站跳转时不携带它们。

本机入口 `.\canvas.cmd` 默认使用项目的 `.local/` 配置；全局安装默认 `%LOCALAPPDATA%\CanvasStudy`。可用 `--home` 或 `CANVAS_STUDY_HOME` 选择配置目录。`config.json` 的 `base_url` 默认 SJTU Canvas；修改站点后须重新登录并重新设置对应课程绑定。其他 Canvas 部署尚未实测。

项目目录和 Python 包暂保留 `canvas-study` 名称以兼容 0.1，本版命令和核心职责已改为通用资源接口。

[项目比较与架构](docs/RESEARCH.md) · [验证记录](docs/VALIDATION.md)
