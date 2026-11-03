# 课堂视频接口

0.3 将课堂视频作为可组合资源提供。课程、视频 ID、日期范围和输出路径均来自参数，没有绑定某门课，也不默认生成笔记。复用 `login` 保存的 Canvas 登录状态。

## 命令

| 命令 | 用途 |
| --- | --- |
| `videos tools COURSE` | 列出外部工具入口，排查视频工具匹配 |
| `videos list COURSE` | 全部分页的视频节次，默认简洁元数据；`--raw` 保留原始对象 |
| `videos get COURSE RECORDING_ID` | 当前课程内单节视频的原始元数据 |
| `videos transcript COURSE RECORDING_ID` | 已有语音文字稿；默认 JSON 数据，终端显示简要状态 |
| `videos subtitles COURSE --output DIR` | 批量导出文字稿与字幕 |
| `videos search COURSE QUERY` | 检索已有文字稿，返回时间戳与原文 |
| `videos slides COURSE RECORDING_ID` | 投影截图时间点、图片链接及已有 OCR 原始数据 |

`COURSE` 支持课程 ID 或已绑定的别名；`RECORDING_ID` 必须来自该课的 `videos list`，与 Canvas 课程 ID 不同。入口自动发现不唯一时，先执行 `videos tools`，再给其他命令加 `--tool-id ID`。

`list`、`subtitles`、`search` 支持 `--since YYYY-MM-DD`、`--until YYYY-MM-DD`、`--latest N`；日期边界包含当天，按开课时间从新到旧筛选后取最近 N 节。不提供范围则处理所有可访问节次。批量请求串行执行，遇到没有文字稿的节次保留缺失记录并继续。

## 输出

单节 `transcript --format json|txt|srt|vtt`；批量 `subtitles --formats json,txt,srt,vtt`，默认 `json,txt,srt`。

- JSON：`schema_version`、`course_id`、`recording`、`source`、`status`、`time_unit`、`sentences`、`paragraphs`、`raw`、`partial`、`warnings`。
- `sentences` / `paragraphs`：`start_ms`、`end_ms`、`text`、`source_index`，单位毫秒，原始记录仍保存在 `raw`。
- TXT：优先使用平台整理后的段落，逐段显示起始时间；没有段落时回退到句子。
- SRT / VTT：优先逐句字幕；导出时修正无效结束时间并裁剪相邻重叠，不改写 JSON 中的原始时间戳。
- 检索：大小写不敏感的字面包含匹配，优先检索段落；返回节次、开课时间、起止毫秒、可读时间点及原文，不执行语义改写。
- `slides`：保留平台 `data`（含 `docList` 等），提供 `status` / `partial` / `warnings`，不额外下载图片。

根级 `--json` 放在 `videos` 前，stdout 使用 `{"ok":true,"data":...}`。查询的 `--output` JSON 文件保存 data 本身。文本格式保存到指定文件；未指定输出时可直接输出到终端。批量导出文件名包含开课时间、课程名和视频 ID；相同内容跳过，不同内容标记冲突，只有 `--overwrite` 才覆盖。

退出码 `0` 表示完成，`2` 表示请求或参数错误，`3` 表示有缺失或冲突；调用者同时检查 `partial` 与 `warnings`。`ok:true` 表示取得了结果对象，不代表每节都存在文字稿。

## 认证与局限

Cookie 登录沿课程的 LTI 入口完成认证；API Token 登录采用 Canvas `sessionless_launch`。LTI 认证需要提交登录表单，课程和视频数据获取使用 GET。只跟随受支持 Canvas / 视频站点的 HTTPS 登录路径；Canvas 凭据不发送到视频站点，视频 JWT 仅存于本次进程内存。

当前适配 `oc.sjtu.edu.cn` 与 `v.sjtu.edu.cn` 新版课堂视频。真实账号已验证 Cookie 流程；Token 启动流程目前只有模拟测试。学校修改内部接口或课程未关联录像教学班时，可能需要更新适配器。

文字稿、投影 OCR 的可用性受平台生成情况、发布状态及账号权限影响，暂无数据不代表登录失败。工具读取已有结果，不启动转写任务，不提供离线音频识别，也尚未实现完整视频媒体下载。自动识别中的术语、公式和人名需对照原视频复核。列表里的 `transcript_status_code` 是平台原始状态码，不据此推断文字稿一定可用。
