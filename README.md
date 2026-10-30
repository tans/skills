# Skills

按任务选择技能，可用 `$技能名` 明确调用。`laoluo` 也会在用户征求意见时自动触发。

| 技能 | 用途 |
| --- | --- |
| `$jiege-hackathon` | 通过提问完善想法，再做成可运行的小产品 |
| `$jiege-imgen` | 生成、编辑并接入项目图片 |
| `$image` | 通过可配置的图片 API 生成、编辑图片并保存到本地 |
| `$jisuhudong-deploy` | 通过 SSH 和宝塔部署或更新 `*.jisuhudong.com` 产品 |
| `$laoluo` | 征求意见时主动挑漏洞、举反例，用锋利但讲理的表达推动观点碰撞 |

## 使用

```text
使用 $jiege-hackathon
我想做一个员工报修工具。
```

`$jiege-hackathon` 会先提问、整理项目摘要，确认后再开始实施。`$jiege-imgen` 可直接描述你想要的图片，或提供参考图。

```text
使用 $laoluo
我想做一个面向所有人的 AI 工具，你觉得怎么样？
```

`$laoluo` 先给判断，再指出关键问题、举例说明并给出建议。说得对就认，不为抬杠而抬杠；单纯执行任务或需要安慰时不自动触发。

## 复制给 Agent

```text
请使用 <$技能名> 完成：<任务>。
```

将 `<$技能名>` 替换为上表中的技能名，例如 `$laoluo`。

## 安装技能

```text
请从 https://github.com/tans/skills 安装 laoluo 技能。
```

也可以从镜像 https://gitee.com/tans/skills 安装，将 `laoluo` 替换为所需技能名。
