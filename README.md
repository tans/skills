# Skills

在任务开头明确写出技能名即可使用。技能不会自动触发。

| 技能 | 用途 |
| --- | --- |
| `$jiege-hackathon` | 通过提问完善想法，再做成可运行的小产品 |
| `$jiege-imgen` | 生成、编辑并接入项目图片 |

## 怎么用

```text
使用 $jiege-hackathon
我想做一个员工报修工具，项目在 /path/to/project。
```

`$jiege-hackathon` 会先提问、整理项目摘要，确认后再开始实施。`$jiege-imgen` 可直接描述你想要的图片，或提供参考图。

## 复制给 Agent

```text
使用 <技能名>。

目标：<想做什么>
项目路径：<项目路径或“新建项目”>
补充：<已有技术、参考图或限制>
验收：<完成后要看到什么>

请先读取对应技能的 SKILL.md 并按要求执行。
```

将 `<技能名>` 替换为 `$jiege-hackathon` 或 `$jiege-imgen`。
