# 调研与设计选择

## 0.2：以通用资源接口为核心

用户明确要求工具可用于不同课程和任务，因此把“数据获取”和“业务加工”拆开：

- `client.py`：认证范围、HTTP、分页、重试；不写文件。
- `resources.py`：作业、附件和题目的组合抓取、HTML 结果页解析；不依赖 Markdown、PDF 或目录绑定。
- `resource_cli.py`：按资源提供 list/get/fetch/download 等命令，原样返回 API 数据。
- `catalog.py` / `download.py`：可选的本地归档流程。
- `review.py`：可选的 Markdown 导出消费者，依赖位于 export extra 中。

获取资源时不需要 bind。sync 默认不抓测验题目、不生成 Markdown。课程 ID、学科、学期和路径均为输入或用户配置。原始作业说明、评分规则、提交类型和复杂题型字段不会在资源查询层被裁剪；应用可以按需要自行转换。

下方保留初版的调研依据与同步实现取舍；0.2 将它们组合成可复用的工具能力。

调研日期：2026-11-03。四个指定项目均已读取 README 和相关源码；没有执行它们的安装脚本或第三方业务代码。本项目独立实现，未复制上游源码。`research/` 是本地只读参考副本，已被版本控制忽略。

## 指定项目

| 项目 | 观察到的实现 | 本项目采用的思路 / 差异 |
| --- | --- | --- |
| [Neko-Yukari/canvas-sjtu-skill](https://github.com/Neko-Yukari/canvas-sjtu-skill) | Playwright 手动登录后保存 storage state；API 请求复用会话；课程关键词查找 | 保留“一次手动登录、后续 HTTP 请求”的流程；验证身份 API 后才保存，只保存 Canvas 相关 Cookie，并使用 Windows DPAPI |
| [xhh678876/sjtu-canvas](https://github.com/xhh678876/sjtu-canvas) | Python requests + Token，课程 / 作业 / 文件 API，文档文字提取 | 采用小型 Python CLI 和 Markdown 导出方向；补上文件版本校验、失败清单与测验题目提取 |
| [wuyutanhongyuxin-cell/SJTU_CLI](https://github.com/wuyutanhongyuxin-cell/SJTU_CLI) | Rust 多校园系统工具；Canvas 子模块使用 PAT，与其他系统 jAccount 会话分开；结构化输出 | 借鉴统一错误输出与明确的只读接口边界；当前专注 Canvas 资料和复习，保持安装轻量 |
| [ZHS-XER/CanvasKit](https://github.com/ZHS-XER/CanvasKit) | Python httpx / Typer / Rich；课程目录绑定、来源聚合、同步清单；另有视频字幕接口 | 借鉴目录绑定、多来源发现、命令行和 JSON 双输出；增强续传、内容校验和作业完整性报告 |

源码核对发现：Neko 的登录成功判定主要看页面 URL，且保存完整浏览器 storage state；xhh 文件下载跳过条件主要是文件已存在；SJTU_CLI 的 Canvas planner 当前代码注明未实现 Link 分页；CanvasKit 下载代码使用临时文件，但不是 Range 断点续传。以上只描述此次读取的版本，不推断未来版本。

## 补充搜索

- [sugarfolds/lecture-notes-pipeline](https://github.com/sugarfolds/lecture-notes-pipeline)：已读取资料下载源码。文件库、单元、作业附件三路发现，以及 manifest / 状态记录适合此场景。本项目也区分采集快照与下载清单；不直接抽取浏览器 Cookie 数据库。
- [Okabe-Rintarou-0/SJTU-Canvas-Helper](https://github.com/Okabe-Rintarou-0/SJTU-Canvas-Helper)：已阅读项目说明。桌面功能更广，包含文档预览、视频和 MCP；适合需要图形界面的用户。本项目侧重可组合的 CLI。
- [JovianSJTU/sjtu-canvas-downloader](https://github.com/JovianSJTU/sjtu-canvas-downloader)：已阅读说明。专注视频 / 字幕及 aria2 任务，适合作为以后录播模块的参考。
- [leinfinitr/sjtu-canvas-skill](https://github.com/leinfinitr/sjtu-canvas-skill)：已阅读说明。把课件与字幕组织为复习上下文，可作为后续“按讲复习”的产品方向。

没有按 Star 数量选方案。对于这次需求，稳定的 HTTP 客户端、可核对的本地状态和明确的题目缺失记录，比集成更多校园服务优先。

## 架构

```text
CLI / JSON
  ├─ 配置与课程别名
  ├─ 认证：Token 或手动登录 Cookie → Windows DPAPI
  └─ Canvas HTTP 客户端：分页、重试、身份范围控制
       ├─ 发现：文件库 + 单元文件/页面 + 大纲 + 作业附件
       ├─ 测验：已完成尝试 API → 可见结果 HTML 回退
       ├─ 下载：版本目标名 + 临时文件 + Range + SHA-256
       └─ 复习：HTML/公式转 Markdown + PDF 文字 + 完整性清单
```

Canvas 资源 HTTP 客户端仅提供 GET；0.3 视频模块额外支持必要的 LTI 登录表单 POST，视频数据仍通过 GET 获取；文件 CDN 跳转时不会携带 Canvas Token 或 Cookie。API 分页必须保持相同站点。课程写入受路径边界检查和进程锁保护，单个文件失败不影响其他文件。默认串行下载，降低服务端压力；暂不实现高并发和自动定时任务。

## 接口依据

- [Canvas 分页文档](https://developerdocs.instructure.com/services/canvas/basics/file.pagination)：跟随 Link 的 next，而不是把 per_page=100 当作全部结果。
- [文件接口](https://developerdocs.instructure.com/services/canvas/resources/files)：下载前重新取得文件元数据和有效下载地址。
- [测验题目接口](https://developerdocs.instructure.com/services/canvas/resources/quiz_questions)：读取历史尝试时同时传 quiz_submission_id 和 quiz_submission_attempt。
- [测验提交接口](https://developerdocs.instructure.com/services/canvas/resources/quiz_submissions)：只使用读取接口，选择已有完成记录。

## 版本溯源

| 仓库 | 读取的 commit |
| --- | --- |
| Neko-Yukari/canvas-sjtu-skill | d21927de783f93126f4b70c11881c10bba6425d6 |
| xhh678876/sjtu-canvas | 18e6c51e3ccfb337c2cae53b1cfb5883dc012293 |
| wuyutanhongyuxin-cell/SJTU_CLI | b4a30458fe48e2d20f453b0165338447102a4085 |
| ZHS-XER/CanvasKit | bcad9b3ea88b813d885af4cbc76c09f27f6ec082 |
| sugarfolds/lecture-notes-pipeline | 3f6c9be733afb29954c87f91b1558a72b881752e |

Neko 仓库此次未发现 LICENSE，故只参考公开设计思路，不移植源码。其他仓库仍各自保留其许可。本项目以后引入上游代码时须单独核对并保留许可和署名。

## 后续优先级

1. 用实际 SJTU 账号验证接口权限与结果页 HTML，补充脱敏回归样本。
2. 对扫描附件增加可选本地 OCR / 页面渲染，复核电路图与公式。
3. 完善 matching / dropdown 等复杂题型、New Quizzes 和按单元筛选。
4. 录播字幕已在 0.3 提供；后续按需要扩展跨平台系统凭据库。


## 0.3 视频适配器

参考 [Canvas 外部工具 API](https://developerdocs.instructure.com/services/canvas/resources/external_tools) 与 [CanvasKit](https://github.com/ZHS-XER/CanvasKit) 的公开端点线索，独立实现 LTI 认证、视频分页、文字稿标准化、字幕序列化与检索。

实测发现本账号 Cookie 对 sessionless_launch 返回 401，而正常课程接口有效；因此 Cookie 使用课程外部工具页面的 LTI 表单，Token 保留 sessionless_launch 分支。视频工具 ID 动态发现，不固化为参考项目中的常量。无文字稿时平台可能返回 ok=true、dataOk=false、data=null，应报告暂无数据，而非要求用户反复登录。

`video.py` 负责学校视频协议；`transcripts.py` 是无网络/文件 I/O 的标准化与字幕处理；`video_cli.py` 提供可组合命令。完整原始文字稿字段仍保留，文本导出是显式操作。
