# 离线示例

`catalog.demo.json` 是人工构造的测试数据，不是从课程 93742 下载的作业。用于查看输出格式和缺失提示。

在项目目录运行：

```powershell
New-Item -ItemType Directory -Force demo-output\.canvas | Out-Null
Copy-Item examples\catalog.demo.json demo-output\.canvas\catalog.json
.\canvas.cmd export review 100 --output demo-output
```

示例故意保留一项未开放测验，因此退出码为 3，表示文档已生成但有缺失。查看 `demo-output/复习/作业题目汇总.md`。

