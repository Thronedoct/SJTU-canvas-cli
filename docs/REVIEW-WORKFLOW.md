# 可选工作流：资料归档与 Markdown 复习导出

把 Canvas 的课件、作业附件和可访问的测验题目整理到自己的课程文件夹。

一个本地 Python CLI。中文帮助、可重复同步、离线复习、JSON 输出。第一版针对 Windows 和 SJTU Canvas；普通数据请求直接走 HTTP，浏览器只在选择会话登录时使用。

## 现在就用

当前电脑已安装项目依赖，并将课程 **93742** 绑定为 **数集**，保存目录为 `C:\Users\pc\Documents\课本\大三上课本\数集`。在本项目目录打开 PowerShell：

```powershell
# 首次登录，推荐 Token：隐藏输入，验证后加密保存
.\canvas.cmd login

# 或使用浏览器手动登录：新开独立 Edge 窗口，完成后回终端按 Enter
.\canvas.cmd login --browser

# 先看待同步项目；这一步不写课程目录
.\canvas.cmd sync 数集 --dry-run

# 下载课件、附件，并生成作业题目汇总
.\canvas.cmd sync 数集 --review

# 后续离线重新生成题集
.\canvas.cmd export review 数集
```

Token 来自 [Canvas 账户设置](https://oc.sjtu.edu.cn/profile/settings) 的访问许可证。请直接在终端输入，不把 Token 发到聊天中。浏览器模式不读取现有浏览器的数据库、不保存密码，只保存你在独立窗口登录后取得的 Canvas Cookie。

API Token 通常适合长期同步；有些测验结果页需要 Cookie，会话登录对这类题目的读取更有帮助。会话过期时重新 `login --browser` 即可。

## 命令

| 命令 | 用途 |
| --- | --- |
| `canvas login` | 验证 Token 并保存加密凭据 |
| `canvas login --browser` | 手动登录，保存 Canvas 会话 |
| `canvas courses` | 列出活跃课程及 ID |
| `canvas bind 93742 "D:\学习\数集" --name 数集` | 课程目录和简短别名绑定 |
| `canvas sync 数集 --dry-run` | 预览下载计划 |
| `canvas sync 数集 --review` | 增量下载并生成题集 |
| `canvas sync 数集 --all-files` | 额外包含压缩包、视频、安装程序 |
| `canvas sync 数集 --no-review` | 只同步资料，不读取测验题目 |
| `canvas export review 数集` | 从本地快照离线生成 Markdown |
| `canvas status` | 查看已绑定课程和采集时间 |
| `canvas doctor --online` | 验证配置、网络和登录 |
| `canvas logout` | 删除本工具保存的凭据，不撤销网站 Token |
| `canvas --json sync 数集` | 结构化输出，适合脚本与 Agent |

本机便携入口为 `.\canvas.cmd`。它固定使用项目下 `.local/` 保存配置，避免终端换目录后丢失绑定。安装成全局命令时默认配置目录为 `%LOCALAPPDATA%\CanvasStudy`，可通过 `--home` 或 `CANVAS_STUDY_HOME` 显式统一。

## 文件布局

```text
课程目录/
├── 课件/
│   ├── 01_Introduction/
│   └── 02_Device/
├── 作业附件/
├── 复习/
│   └── 作业题目汇总.md
└── .canvas/
    ├── catalog.json       # 课程内容快照；不存 Token 或文件签名下载链接
    ├── manifest.json      # 成功下载文件、版本、大小、SHA-256
    ├── last-run.json      # 每项处理状态与失败原因
    └── sync.lock          # 同一课程的进程锁
```

文件名保留原名称，再附文件 ID 和版本短码，避免同名文件覆盖。文件内容更新会保存新版本；旧版本保留。重复同步通过 SHA-256 核对已下载内容。已有未纳入清单的同名文件、被你改动的文件均不会自动覆盖。当前版本不会凭“文件名相同”收编旧下载，首次同步可能与原目录里的旧课件并存。

默认包含常见文档、课件、模型文件及作业附件；视频、安装程序和普通压缩包需要 `--all-files`。同一文件在多个单元出现时只下载一次，清单保留全部来源，实体放在第一个来源单元。

下载先写 `.part`。存在服务器版本校验标记时支持 Range 续传；不满足条件时从头下载。只有大小符合 Canvas 元数据才完成落盘并写入校验记录。临时网络错误和限流会有限重试；再次执行相同命令即可恢复未完成项。

## 题集的范围与完整性

- 采集作业说明、附件链接、当前账号已完成测验尝试中的题干和选项；不开始新测验、不提交答案。
- 学生题目 API 无权限时，会话模式尝试读取当前账号可见的结果页。API Token 不能保证替代网页 Cookie。
- 保留 LaTeX 公式和 Canvas 文件图片链接；已下载的题图改为本地相对路径。
- PDF 附件按页提取全文，保留原件链接。计算题编号仍以作业说明为准，不自动猜题号边界。PDF 图表、复杂公式、扫描页需要查看原件；未集成 OCR。
- 问卷单独标注。题目数量不符、附件缺失、在线图片未本地化、复杂题型都会写入文末“完整性检查”。这不是“全部题目完整导出”的保证。
- Canvas New Quizzes / 外部 LTI 测验没有专门适配。课程隐藏文件和未开放题目遵循服务端权限。

若网页能显示题目而自动接口不能读，可保存测验结果 HTML，命名为 **作业ID.html**，再执行：

```powershell
.\canvas.cmd export review 数集 --html-dir "D:\学习\Canvas结果页"
```

离线生成会按该 ID 用保存的题目替代未取得的 API 题目。只读取 HTML，不执行其中的脚本。其他站点的图片保留联网引用并标注；不会向第三方图片站点发送 Canvas 凭据。

## 脚本调用约定

`--json` 放在子命令前，stdout 为结果 JSON，普通进度写 stderr。成功结构为 `{"ok":true,"data":...}`；操作错误结构为 `{"ok":false,"error":{"message":"..."}}`。参数语法错误沿用 CLI 标准 stderr 提示。

退出码：`0` 完成；`2` 配置、认证、网络或参数错误；`3` 已产生结果但有缺失或失败；`130` 用户中断。退出码 3 时检查 `partial`、`warnings`、`files[].error` 和 `review.issues`。

## 安装与开发

需要 Python 3.11+ 和 [uv](https://docs.astral.sh/uv/)。

```powershell
uv sync --extra browser --extra export --group dev
.\canvas.cmd --version
.\canvas.cmd doctor

# 如需全局命令（可选），在项目目录执行
uv tool install --editable . --with playwright --with markdownify --with pypdf
```

浏览器登录默认调用本机 Edge，不必下载另一份 Chromium。也可 `login --browser --channel chrome`。其他操作不依赖浏览器。Windows 持久凭据由当前用户的 DPAPI 加密；其他系统当前仅支持 `CANVAS_TOKEN` 环境变量，不支持持久凭据登录。

测试无需 Canvas 账号：

```powershell
uv run pytest -q
uv run ruff check src tests
```

在受限临时目录环境，可使用项目内新建的独立临时测试目录；详见 [验证记录](VALIDATION.md)。

[设计与项目比较](RESEARCH.md) · [离线演示](../examples/README.md)

