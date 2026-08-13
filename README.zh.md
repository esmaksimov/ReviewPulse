# ReviewPulse

[English](README.md) · [Русский](README.ru.md) · [Español](README.es.md) · [Italiano](README.it.md) · [中文](README.zh.md)

一个防止代码评审卡住的 Telegram 机器人：它跟踪每个指定评审人的状态，并在工作时间内私信提醒当前轮到谁处理。

它能识别两种情况：

1. 评审人一直没有做出任何反应，MR 就这样一直挂着。
2. 评审人提出了修改意见，作者已经全部修复并关闭了讨论串，但评审人一直没有回来批准。理论上轮到他了，但没人提醒他。

如果评审人在修复之后**又一次**要求修改，轮次会回到作者身上，提醒也会停止——机器人绝不会去打扰一个已经完成自己那部分工作的人。

---

## 快速开始

现成镜像：[`s1k0de/reviewpulse`](https://hub.docker.com/r/s1k0de/reviewpulse)
（linux/amd64 + linux/arm64）。不需要自己构建。

**1. 创建机器人**：通过 [@BotFather](https://t.me/BotFather) 创建并复制 token。顺便执行
`/setprivacy` → **Disable**——否则机器人看不到 Telegram 自动复制到讨论组里的帖子。

**2. 添加机器人**：把它设为评审频道的管理员，**同时**加入其关联的讨论组（评论功能必须开启）。

**3. 启动它**——根据你的部署方式二选一：

<table>
<tr><th>docker run</th><th>docker compose</th></tr>
<tr valign="top"><td>

```bash
docker run -d --name reviewpulse \
  --restart unless-stopped \
  -e BOT_TOKEN='你的令牌' \
  -e TIMEZONE_OFFSET_HOURS=3 \
  -e WORK_START=09:00 -e WORK_END=18:00 \
  -v reviewpulse-data:/app/data \
  s1k0de/reviewpulse:latest
```

</td><td>

```bash
curl -O https://raw.githubusercontent.com/\
esmaksimov/ReviewPulse/main/docker-compose.yml
curl -o .env https://raw.githubusercontent.com/\
esmaksimov/ReviewPulse/main/.env.example
# 在 .env 中填入 BOT_TOKEN
docker compose up -d
```

</td></tr>
</table>

推荐保留 compose 方式——以后想改配置时改的就是它，而且 `--profile postgres`
只需一个参数就能切换（见下方[用哪种数据库](#用哪种数据库)）。

其余选项都有合理的默认值：SLA 为 2 小时，每 20 分钟提醒一次，每天最多 8 次提醒，当每个被指定的评审人都通过后评审即关闭。完整列表见
[`.env.example`](.env.example)。

**4. 每个评审人都要先给机器人发一次 `/start`。** Telegram 不允许机器人主动发起私聊——不这样做提醒就永远无法送达。如果某个被指定的评审人还没这样做，机器人会在评论串里提醒一次。

### 用哪种数据库

**用 SQLite 就行**——这是默认选项，也完全够用。机器人是单进程的，每天只写入几十行数据；整个数据库就是卷上的一个文件，备份只需要 `cp`。

只有在你已经有一个 Postgres 实例，或者想要现成的备份方案时，才值得用它：

```bash
docker compose --profile postgres up -d
```
并在 `.env` 中设置：
```dotenv
DATABASE_URL=postgresql+asyncpg://reviewpulse:reviewpulse@db:5432/reviewpulse
```

两种数据库使用相同的表结构和迁移，都经过了完整测试。迁移会在启动时自动执行。

---

## 效果展示

机器人是按帖子的结构来解析的，而不是死板的模板——下面两种写法都能识别。要让一条帖子被视为评审，只需满足以下两点之一：**至少包含一个 MR 链接**，或者**有一行明确标注的评审人列表**（这样即使是纯文档或纯基础设施的改动、完全没有
MR，只要明确指定了评审人，也会被跟踪）。两者都没有的帖子会被当作普通公告，不会被跟踪。

> 标签（"评审:"、"MR:"、"文档:" 等）在机器人所支持的每一种语言里都能被识别——无论团队用俄语、英语还是中文写帖子，解析结果都是一样的。见[语言支持](#语言支持)。

频道中的一条帖子：

```
支付

连接池优化

MR SC: https://gitlab.example.com/backend/services/api_controller/-/merge_requests/1112

MR Utils: https://gitlab.example.com/backend/packages/utils/-/merge_requests/223

任务: https://tasks.example.com/space/2829/boards/card/3517380

评审: @user1 @user2
```

更严格的模板也能正确解析：

```
商品目录
修复支付跳转问题

MR: https://gitlab.example.com/backend/services/checkout/-/merge_requests/77

文档: https://wiki.example.com/pages/12345
描述: 如果缺少文档
评审人: @user2 负责后端 / @user1 负责其余部分，@user3
```

机器人从帖子中提取的内容：

| 字段 | 来源 |
|---|---|
| 产品名 | 第一行非空内容 |
| 任务标题 | 之后第一行不是标签也不是纯链接的内容 |
| MR | 所有形如 `…/-/merge_requests/<N>` 的链接，**全部**提取，不管有多少个 |
| 评审人 | "评审…" 那一行里的所有 `@用户名`；如果没有这一行，则取帖子中所有的 `@用户名` |

任务系统或 wiki 的链接不会被误判为 MR。评审人那一行里夹杂的其他文字也不会掩盖用户名。如果无法识别出评审人，机器人不会保持沉默：卡片会带上一个"🙋 我是评审人"的按钮。

帖子下方评论串里出现的卡片：

```
🤖 支付 — 连接池优化

   通过数：1/2

   • @user1 — 👍 已通过
   • @user2 — 🔁 修改已完成，等待复查

   api_controller!1112
   utils!223

   [👍 通过]        [✍️ 需要修改]
   [✅ 已修复]      [🗄 关闭]
```

而当前轮到谁处理，谁就会收到一条私信：

```
🔁 修改已完成，但 ✍️ 状态未变

作者已经处理了你提出的全部意见，但你还没有给出通过。

支付 — 连接池优化
已超时：1小时20分钟（工作时间）

https://gitlab.example.com/backend/services/api_controller/-/merge_requests/1112

打开讨论

   [🔕 1小时]  [🔕 明天再说]
```

---

## 为什么用按钮而不是表情回应

**Telegram 频道里的表情回应是匿名的。** 作为管理员的机器人只能拿到
`message_reaction_count`（汇总数据，比如"👍 2"），而拿不到带有 `user`
字段的 `message_reaction`——按用户区分的回应只存在于群组和超级群组中。无论是机器人还是通过 MTProto
的 userbot，都无法知道究竟是*谁*给频道帖子点了回应；Telegram 根本不会提供这个信息。
参见 [Bot API](https://core.telegram.org/bots/api#update) 和
[api/reactions](https://core.telegram.org/api/reactions)。

所以机器人改为在评论串里发布**自己的带内联按钮的卡片**。`callback_query`
永远携带 `from.id`，因此每个评审人的状态都 100% 可靠。频道本身和帖子格式都不需要改变。

工作原理：

```
频道中的帖子
     │
     │  Telegram 自动将其复制到关联的讨论组
     ▼
讨论组中的副本  ──►  机器人回复它，  ──►  卡片最终落在
                    发送带按钮的卡片      评论串内部
                            │
                            │  点击按钮 = callback_query，
                            ▼  其中始终携带 from.id
                     每个评审人的状态都可靠
```

两个更新——频道帖子本身和讨论组里的副本——到达的顺序是不确定的，所以两条处理路径都会针对同一个键做
upsert，谁后到达，谁就真正发布卡片。

---

## 状态模型

状态存储在**（评审 × 评审人）**这一对组合上，而不是存在评审本身上——一个评审人可能已经通过了，而另一个还在等待处理。

| 状态 | 含义 | 轮到谁 | 是否提醒 |
|---|---|---|---|
| `PENDING` | 还没有结论 | 评审人 | 是，超过 SLA 后 |
| `CHANGES_REQUESTED` | ✍️，修复还没做 | 作者 | 否 |
| `AWAITING_RECHECK` | 修复已完成，✍️ 状态未变 | 评审人 | 是，超过 SLA 后 |
| `APPROVED` | 👍 | — | 否 |

```
PENDING           --[👍]------------------> APPROVED
PENDING           --[✍️]------------------> CHANGES_REQUESTED
CHANGES_REQUESTED --[标记修复完成]---------> AWAITING_RECHECK
AWAITING_RECHECK  --[👍]------------------> APPROVED
AWAITING_RECHECK  --[✍️]------------------> CHANGES_REQUESTED   ← "又要求修改"
APPROVED          --[✍️]------------------> CHANGES_REQUESTED   ← 撤销误点的通过
```

倒数第二条转换就是"评审人查看了修复内容又要求了更多修改"的情况：轮次回到作者身上，提醒停止，SLA
计时器重置。

实现见 [`domain/state.py`](src/reviewpulse/domain/state.py)，一个不涉及任何 I/O 的纯逻辑模块。

### 一次评审需要多少个通过

`REQUIRED_APPROVALS`（默认 2）是一个**上限**，而不是固定目标。实际需要的通过数会根据帖子中实际指定的评审人数量自动调整：

- 只指定了一个评审人 → 他一个人的通过就足以关闭评审——不会去等一个永远不会出现的第二个结论；
- 指定了两个 → 两人都必须通过；
- 指定的人数超过上限 → 依然只需要 `REQUIRED_APPROVALS` 个通过，长长的评审人名单不会变成"必须全员一致通过"的要求。

实现见 [`services/reviews.py:approvals_needed`](src/reviewpulse/services/reviews.py)。

---

## 工作时间

SLA（2 小时）和重复提醒间隔（20 分钟）都**只在工作时间窗口内**计时——默认为
UTC+3 的 09:00–18:00，周一到周五。周五 17:30 发的帖子，截止时间会落在周一 10:30。夜间和周末不计入时间。

时间计算逻辑见 [`domain/workhours.py`](src/reviewpulse/domain/workhours.py)。目前还不考虑法定节假日；`is_working_day`
是预留的扩展点。

**防打扰机制：** 每对（评审×评审人）每天最多 `MAX_NUDGES_PER_DAY`（8）次提醒，每条私信都带有"🔕
1小时"/"🔕 明天再说"按钮，还有 `/mute 2h` 命令。已经屏蔽机器人的用户会直接从查询中被排除，而不是每分钟重试一次。

---

## 机器人如何知道修改已经完成

**模式 A（默认）。** 作者在卡片上点击"✅ 已修复"——所有还处于 ✍️
状态的评审人都会转为 `AWAITING_RECHECK`。

**模式 B（`GITLAB_ENABLED=true` + token）。** 机器人自己轮询 GitLab 并读取讨论串：

- 评审人发起的所有可解决讨论串，在评审所涉及的**全部** MR 中都已解决 → "修改已完成"；
- 出现了一条他发起的新的未解决讨论串 → "又要求修改了"，提醒随之停止。

第二条规则是一种不依赖按钮、独立判断评审人是否回来的方式：即使他只在 GitLab
里留了评论、从没碰过卡片，机器人也会自动安静下来。

评审人可以通过 `/link <用户名>` 关联自己的 GitLab 账号。没有这个映射关系时，机器人会退而使用整个 MR
级别的 `blocking_discussions_resolved` 标志——粒度更粗，但总比什么都没有强。同步逻辑永远不会去改动已经通过的评审人，这样别人重新打开的讨论串就不会悄悄撤销一个
👍。

---

## 语言支持

支持语言：俄语、英语、西班牙语、意大利语、中文（`ru`、`en`、`es`、`it`、`zh`）。

有两类不同的内容需要语言设置，它们并不共用同一个：

- **共享卡片和"请 /start"提示**位于评论串中——所有看到它的人看到的都是同一条消息，所以它们只有一种语言：`DEFAULT_LOCALE`（默认为
  `en`）。
- **私信**——提醒、`/start`、`/status`、按钮点击后的确认——则跟随每个评审人自己的语言：优先用他通过
  `/lang` 设置的语言，否则用 Telegram 客户端自身的语言，再否则用 `DEFAULT_LOCALE`。

```bash
/lang zh   # 把你自己收到的私信切换为中文
/lang      # 列出支持的语言
```

翻译表位于 [`telegram/texts.py`](src/reviewpulse/telegram/texts.py)；语言解析逻辑位于
[`i18n.py`](src/reviewpulse/i18n.py)。有一个测试专门验证这五种语言拥有完全相同的 key
集合，因此某条文案只加到了一种语言、却忘了加到另一种语言时，会直接导致 CI
失败，而不是在生产环境里悄悄退化成英文。

---

## 机器人命令

| 命令 | 作用 |
|---|---|
| `/start` | 完成注册；把你的 @用户名关联到你的 id，并查找当前等待你处理的评审 |
| `/status` | 查看当前挂在你身上的评审、截止时间，以及每条评审对应帖子的链接 |
| `/link <用户名>` | 关联你的 GitLab 账号（用于模式 B） |
| `/lang <代码>` | 切换机器人发给你私信时使用的语言 |
| `/mute 2h`、`/unmute` | 暂停提醒 / 恢复提醒 |

---

## 开发

```bash
poetry env use 3.12
poetry install
cp .env.example .env                # 填入 BOT_TOKEN
poetry run python -m reviewpulse    # 迁移会在启动时自动执行

poetry run pytest                   # 122 个测试
poetry run ruff check src tests
```

覆盖了最关键的部分：工作时间计算（周五 17:30 → 周一 10:30）、状态机的每一条转换（包括"又要求修改"）、动态所需通过数规则、帖子解析器（针对真实帖子格式和严格模板两种情况）、基于夹具的
GitLab 讨论串解析、五种语言翻译表的完整性检查，以及一个在真实 SQLite
数据库上完整跑通、且能在重启后保持状态的完整流程。

构建并发布你自己的镜像：

```bash
docker buildx build --platform linux/amd64,linux/arm64 \
  -t 你的账号/reviewpulse:latest --push .
```

**手动端到端验证。** 建一个测试频道并关联一个讨论组，把机器人设为两者的管理员，然后在
`.env` 中加速时间流逝：

```dotenv
SLA_MINUTES=1
RECHECK_SLA_MINUTES=1
NUDGE_INTERVAL_MINUTES=1
WORK_START=00:00
WORK_END=23:59
WORK_DAYS=0,1,2,3,4,5,6
```

这样一来，完整的流程——提醒 → ✍️ → "已修复" → 复查提醒 → ✍️ 再次触发 → 安静 →
👍×2 → 关闭——几分钟内就能走完一遍。

---

## 项目结构

```
src/reviewpulse/
  config.py              从环境变量读取的配置
  i18n.py                 支持的语言列表及其解析逻辑（私信 vs. 共享消息）
  domain/                 纯逻辑：状态机、工作时间、升级提醒规则
  parsing/                 帖子解析与 MR 链接提取
  gitlab/                  REST 客户端与讨论串解析
  db/                      数据模型、会话、查询
  services/                连接领域逻辑与数据库：评审、提醒、GitLab 同步
  telegram/                 机器人、处理器、卡片、多语言文案
  scheduler/                提醒定时任务与同步定时任务
migrations/                Alembic 迁移
```

---

## 已知限制

- **谁点击了"✅ 已修复"不会被验证。** 频道帖子是匿名的——Telegram
  不会上报作者信息——所以这个按钮对讨论串里的任何人都可见可用。
- **机器人看不到帖子本身上的表情回应**（原因见上文）；卡片才是真正的状态来源。
- **关闭后频道里的帖子不会被删除**——机器人只会把自己的卡片状态改为"✅
  已关闭"。删除别人发的帖子会破坏讨论串的历史记录。
- **不考虑法定节假日**——机器人会把国家法定假日当作普通工作日处理。
- **帖子解析针对每个字段只认识一组固定的标签词**（见[效果展示](#效果展示)）——不在这个列表里的标签，无论是什么语言，都会退化为按位置猜测，而不是被直接读取。
