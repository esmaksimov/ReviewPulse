# ReviewPulse

[English](README.md) · [Русский](README.ru.md) · [Español](README.es.md) · [Italiano](README.it.md) · [中文](README.zh.md)

<img width="1774" height="887" alt="code-review-reminder-bot-under-1mb" src="https://github.com/user-attachments/assets/b53f8d62-a9d3-4937-8f22-f3b1c486958f" />

A Telegram bot that keeps code review from stalling: it tracks status per assigned
reviewer and DMs whoever the ball is currently on — strictly during working hours.

It catches two situations:

1. A reviewer never reacted at all, and the MR just sits there.
2. A reviewer asked for changes, the author fixed everything and closed the threads,
   but the reviewer never came back to approve. The ball is on them, but nobody is
   chasing them for it.

If the reviewer asks for changes **again** after the fixes land, the ball goes back to
the author and reminders stop — the bot never nags someone who already did their part.

---

## Quick start

Prebuilt image: [`s1k0de/reviewpulse`](https://hub.docker.com/r/s1k0de/reviewpulse)
(linux/amd64 + linux/arm64). Nothing to build.

**1. Create a bot** with [@BotFather](https://t.me/BotFather) and copy the token. While
you're there: `/setprivacy` → **Disable** — otherwise the bot never sees the post
Telegram auto-copies into the discussion group.

**2. Add the bot** as an administrator of the review channel **and** as a member of its
linked discussion group (comments must be enabled).

**3. Run it** — pick whichever fits how you deploy:

<table>
<tr><th>docker run</th><th>docker compose</th></tr>
<tr valign="top"><td>

```bash
docker run -d --name reviewpulse \
  --restart unless-stopped \
  -e BOT_TOKEN='YOUR_TOKEN' \
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
# fill in BOT_TOKEN in .env
docker compose up -d
```

</td></tr>
</table>

Compose is the one worth keeping around — it's what you edit when you come back to
change a setting, and `--profile postgres` is one flag away if you ever want it (see
[Which database](#which-database) below).

Everything else has a sane default: 2-hour SLA, a reminder every 20 minutes, at most 8
nudges a day, a review closes once every named reviewer has approved. Full list in
[`.env.example`](.env.example).

**4. Every reviewer sends the bot `/start` once.** Telegram forbids a bot from
messaging first — without this, reminders can never arrive. If an assigned reviewer
hasn't done this yet, the bot says so once in the comment thread.

### Which database

**Use SQLite** — it's the default and it's enough. The bot is single-process and writes
a few dozen rows a day; the whole database is one file on a volume, and backing it up
is `cp`.

Postgres is worth it only if you already run one, or want off-the-shelf backups:

```bash
docker compose --profile postgres up -d
```
and in `.env`:
```dotenv
DATABASE_URL=postgresql+asyncpg://reviewpulse:reviewpulse@db:5432/reviewpulse
```

Same schema, same migrations, both backends tested end to end. Migrations apply
themselves on startup.

---

## What it looks like

The bot reads posts by shape, not by a rigid template — both examples below work. It
needs one of two things to treat a post as a review at all: **at least one
merge-request link**, or **an explicitly labelled reviewer line** (so a docs-only or
infra-only change with no MR still gets tracked, as long as reviewers were named on
purpose). Without either, a post is an announcement and is never tracked.

> Label words ("Review:", "MR:", "Docs:", ...) are recognized in every language the
> bot itself speaks — a team posting in Spanish or Chinese gets the same parsing a
> Russian- or English-speaking team does. See [Language support](#language-support).

A post in the channel:

```
Payments

Connection pool rework

MR API: https://gitlab.example.com/backend/services/api_controller/-/merge_requests/1112

MR Utils: https://gitlab.example.com/backend/packages/utils/-/merge_requests/223

Task: https://tasks.example.com/space/2829/boards/card/3517380

Review: @user1 @user2
```

A stricter template also parses fine:

```
Catalog
Fix the payment redirect

MR: https://gitlab.example.com/backend/services/checkout/-/merge_requests/77

Docs: https://wiki.example.com/pages/12345
Description: if documentation is missing
Reviewer: @user2 for backend / @user1 for everything else, @user3
```

What the bot pulls out of a post:

| Field | Source |
|---|---|
| product | first non-empty line |
| task title | next line that isn't a label or a bare link |
| MRs | **every** link shaped like `…/-/merge_requests/<N>`, however many there are |
| reviewers | every `@handle` on the "Review…" line; otherwise every `@handle` in the post |

Tracker and wiki links are never mistaken for MRs. Prose mixed into the reviewer line
doesn't hide the handles. If no reviewers could be identified, the bot doesn't stay
silent — the card ships with a "🙋 I'm a reviewer" button instead.

### Generating the post for you

Instead of typing the whole thing by hand, let the bot compose it: it fills in the
product name and picks reviewers itself, then posts the result to the channel.

Tap **📢 Announce** in the bot's menu (or send a bare `/announce`) and it asks for one
thing per message — title, then MR/PR links, docs, task — with a **⏭ Skip** button on
every optional one. Answering each in its own message is also what makes pasted
hyperlinks work: `Docs: Confluence` where *Confluence* is a link carries no URL in the
message text at all, and reading only the text used to publish a post with the docs
line silently missing.

Two branches follow from what you skip:

- **No MR/PR at all** — an SQL-only fix or a docs change — and the bot asks which
  product it belongs to, since there is no repo to infer one from. The post is still
  tracked: a deliberate reviewer line is enough, no merge request required.
- **No docs link** and it offers a free-text **Description** instead, which goes out
  as the template's `Description:` line.

If you already have the text on your clipboard, the one-shot form still works:

```
/announce Connection pool rework
https://gitlab.example.com/example/demo-project/-/merge_requests/1112
Docs: https://wiki.example.com/pages/1
```

Either way the bot replies with a preview and three buttons — **Publish**, **🔁 Reroll
reviewer**, **Cancel** — so you can redraw the pick before it goes out (the reviewer
it drew happens to be on vacation, say) or back out entirely. Once published, the post
goes through the exact same parsing/tracking path as one typed by hand — nothing about
it is treated specially.

Reviewer selection is env-configured, one entry per GitLab project, keyed by the same
`project_path` an MR URL already carries:

```dotenv
REVIEW_PROJECTS={"example/demo-project":{"product":"Demo Product","techlead":"user1","pool":["user2","user3","user4"]}}
```

- `product` — shown on the generated post.
- `techlead` *(optional)* — always included, unless they're the one running
  `/announce`.
- `pool` — candidates for the remaining slot(s), drawn at random, composer excluded.
- `reviewer_count` *(optional, default 2)* — total reviewers on the post, techlead
  included.

The author line resolves for free here — unlike a hand-typed post, the composer's
identity is already known from the DM, with no opt-in tag needed.

Naming several MR links pulls in several repos at once — fine as long as every one
of them is configured identically in `REVIEW_PROJECTS`. If two named repos disagree
(different product, techlead, or pool), the draft is rejected up front with the
conflicting project names, rather than silently picking one of them.

The card that appears in the comment thread under the post:

```
🤖 Payments — Connection pool rework

   Approvals: 1/2

   • @user1 — 👍 approved
   • @user2 — 🔁 fixes are in, awaiting another look

   api_controller!1112
   utils!223

   [👍 Approve]     [✍️ Request changes]
   [✅ Fixed]        [🗄 Close]
```

And whoever the ball is on gets a DM:

```
🔁 Fixes are in, but ✍️ still stands

The author addressed everything you asked for, but there's no approval from you yet.

Payments — Connection pool rework
Overdue by: 1h 20min of working time

https://gitlab.example.com/backend/services/api_controller/-/merge_requests/1112

Open discussion

   [🔕 1h]  [🔕 Tomorrow]
```

---

## Why buttons, not reactions

**Reactions on a Telegram channel are anonymous.** An admin bot only gets
`message_reaction_count` (the aggregate "👍 2"), never `message_reaction` with a `user`
field — per-user reactions only exist in groups and supergroups. There is no way for a
bot, or a userbot over MTProto, to learn *who specifically* reacted to a channel post;
Telegram simply doesn't hand that out.
See [Bot API](https://core.telegram.org/bots/api#update) and
[api/reactions](https://core.telegram.org/api/reactions).

So the bot posts **its own card with inline buttons** in the comment thread instead. A
`callback_query` always carries `from.id`, so per-reviewer state is 100% reliable. The
channel itself and the post format stay untouched.

The mechanics:

```
post in the channel
     │
     │  Telegram auto-copies it into the linked discussion group
     ▼
copy in the group  ──►  the bot replies to it with  ──►  the card lands inside
                        a card + inline buttons          the comment thread
                            │
                            │  pressing a button = callback_query,
                            ▼  which always carries from.id
                     reliable per-reviewer status
```

Both updates — the channel post and its group copy — arrive in an unpredictable order,
so both code paths upsert against the same key, and whichever lands second is the one
that actually publishes the card.

---

## State model

State lives on the **(review × reviewer)** pair, not on the review — one reviewer can
already have approved while another is still holding the ball.

| State | Meaning | Ball is on | Nudged? |
|---|---|---|---|
| `PENDING` | no verdict yet | reviewer | yes, after the SLA |
| `CHANGES_REQUESTED` | ✍️, fixes not in yet | author | no |
| `AWAITING_RECHECK` | fixes are in, ✍️ still stands | reviewer | yes, after the SLA |
| `APPROVED` | 👍 | — | no |

```
PENDING           --[👍]-------------------> APPROVED
PENDING           --[✍️]-------------------> CHANGES_REQUESTED
CHANGES_REQUESTED --[fixes marked done]----> AWAITING_RECHECK
AWAITING_RECHECK  --[👍]-------------------> APPROVED
AWAITING_RECHECK  --[✍️]-------------------> CHANGES_REQUESTED   ← "asked for more"
APPROVED          --[✍️]-------------------> CHANGES_REQUESTED   ← undo a mis-click
```

The second-to-last edge is "the reviewer looked at the fixes and asked for more": the
ball goes back to the author, reminders go quiet, the SLA clock resets.

Implementation — [`domain/state.py`](src/reviewpulse/domain/state.py), a pure module
with no I/O.

### How many approvals a review needs

`REQUIRED_APPROVALS` (default 2) is a **ceiling**, not a fixed target. The actual
number needed scales down to how many reviewers were actually named:

- name one reviewer → their approval alone closes it — nothing waits on a second
  verdict that was never coming;
- name two → both must sign off;
- name more than the cap → it still only takes `REQUIRED_APPROVALS`, so a long
  reviewer list doesn't turn into a unanimous-approval requirement.

Implementation — [`services/reviews.py:approvals_needed`](src/reviewpulse/services/reviews.py).

---

## Working hours

Both the SLA (2h) and the repeat interval (20min) tick **only inside the working
window** — 09:00–18:00 UTC+3, Mon–Fri by default. A post at 17:30 on Friday has a
deadline of 10:30 on Monday. Nights and weekends contribute nothing.

Arithmetic — [`domain/workhours.py`](src/reviewpulse/domain/workhours.py). Public
holidays aren't accounted for yet; `is_working_day` is the extension point.

**Anti-spam:** at most `MAX_NUDGES_PER_DAY` (8) reminders a day per pair, "🔕 1h" /
"🔕 Tomorrow" buttons on every DM, and a `/mute 2h` command. A user who blocked the bot
is excluded from the query outright rather than retried every minute.

---

## How the bot learns fixes are addressed

**Mode A (default).** The author taps "✅ Fixed" on the card — every reviewer still
sitting on ✍️ moves to `AWAITING_RECHECK`.

**Mode B (`GITLAB_ENABLED=true` + a token).** The bot polls GitLab itself and reads the
threads:

- every resolvable thread the reviewer opened is resolved, across **all** MRs on the
  review → "fixes are in";
- a new unresolved thread from them appears → "asked for more", reminders go quiet.

That second rule is a button-independent way to catch a reviewer coming back: even if
they only left a comment in GitLab and never touched the card, the bot goes quiet.

A reviewer links their GitLab login with `/link <username>`. Without a mapping, the
MR-wide `blocking_discussions_resolved` flag is used instead — coarser, but better than
nothing. Approved reviewers are never touched by the sync, so someone else's reopened
thread can't silently revoke a 👍.

---

## Team stats

A periodic DM digest of two numbers, broken down per person: how long an author took
to address "changes requested" once it landed, and how long a reviewer took to give
their first verdict. Slowest averages listed first.

Nothing is sent unless at least one recipient is configured:

```dotenv
STATS_REPORT_RECIPIENT_IDS=123456789,987654321
STATS_REPORT_INTERVAL_DAYS=7
```

The same digest is available on demand with `/stats` — restricted to the same
recipient list, since it's per-person timing data.

Only counts from the moment this history started being recorded — there's no way to
backfill reviews that closed before it existed.

---

## Language support

Supported: Russian, English, Spanish, Italian, Chinese (`ru`, `en`, `es`, `it`, `zh`).

Two different things need a language, and they don't share one:

- **The shared card and the "please /start" hint** live in the comment thread — every
  viewer sees the same message, so there's exactly one language for them:
  `DEFAULT_LOCALE` (default `en`).
- **DMs** — reminders, `/start`, `/status`, button-press confirmations — follow each
  reviewer's *own* language: whatever they set with `/lang`, otherwise Telegram's own
  client language, otherwise `DEFAULT_LOCALE`.

```bash
/lang es   # switch your own DMs to Spanish
/lang      # list what's supported
```

Translation tables live in [`telegram/texts.py`](src/reviewpulse/telegram/texts.py);
locale resolution in [`i18n.py`](src/reviewpulse/i18n.py). A test asserts all five
locales carry the exact same set of keys, so a string added to one language and
forgotten in another fails CI instead of silently falling back to English in
production.

---

## Bot commands

| Command | What it does |
|---|---|
| `/start` | registers you; links your @handle to your id and finds reviews waiting on you |
| `/status` | what's on you right now, with deadlines and a link to each post |
| `/announce` | put together the channel post for you — see [Generating the post for you](#generating-the-post-for-you) |
| `/link <username>` | link your GitLab account (for Mode B) |
| `/lang <code>` | switch the bot's language for your own DMs |
| `/mute 2h`, `/unmute` | go quiet / start reminding again |
| `/stats` | the team-stats digest, on demand — configured recipients only, see [Team stats](#team-stats) |

---

## Development

```bash
poetry env use 3.12
poetry install
cp .env.example .env                # fill in BOT_TOKEN
poetry run python -m reviewpulse    # migrations apply themselves on startup

poetry run pytest                   # 240 tests
poetry run ruff check src tests
```

Covers the parts that matter: working-hours arithmetic (Friday 17:30 → Monday 10:30),
every edge of the state machine including "asked for more", the dynamic
approvals-needed rule, the post parser against both a real-world post and the strict
template, GitLab thread parsing against fixtures, translation-table completeness across
all five locales, and a full cycle through a live SQLite database that survives a
restart.

Build and push your own image:

```bash
docker buildx build --platform linux/amd64,linux/arm64 \
  -t YOUR_ACCOUNT/reviewpulse:latest --push .
```

**Manual end-to-end run.** Set up a test channel with a linked discussion group, make
the bot an admin of both, and speed up time in `.env`:

```dotenv
SLA_MINUTES=1
RECHECK_SLA_MINUTES=1
NUDGE_INTERVAL_MINUTES=1
WORK_START=00:00
WORK_END=23:59
WORK_DAYS=0,1,2,3,4,5,6
```

Then the full cycle — nudge → ✍️ → "Fixed" → recheck nudge → ✍️ again → silence →
👍×2 → close — plays out in a few minutes.

---

## Structure

```
src/reviewpulse/
  config.py            settings from the environment
  i18n.py               locale list and resolution (DM vs. shared-message language)
  domain/               pure logic: state machine, working hours, escalation rules
  parsing/               post parsing and MR link extraction
  gitlab/                REST client and thread parsing
  db/                    models, session, queries
  services/              domain + DB glue: reviews, nudges, GitLab sync, announcements, stats
  telegram/               bot, handlers, card, announcement & stats rendering, translated copy
  scheduler/              the nudge tick and the sync tick
migrations/              Alembic
```

---

## Known limitations

- **Who pressed "✅ Fixed" isn't verified.** A channel post is anonymous — Telegram
  doesn't report an author — so the button is available to anyone in the thread.
- **The bot can't see reactions on the post itself** (see above); the card is the
  source of truth.
- **The channel post isn't deleted on close** — the bot only flips its card to
  "✅ Closed". Deleting someone else's post breaks the thread's history.
- **Public holidays aren't accounted for** — the bot will treat a national holiday as
  a normal working day.
- **Post parsing recognizes a fixed set of label words** per field (see
  [What it looks like](#what-it-looks-like)) — a label outside that list, in any
  language, falls back to positional heuristics rather than being read directly.
- **`/announce` requires every referenced project to be configured identically** — a
  draft naming MRs from several repos is fine as long as their `REVIEW_PROJECTS`
  entries match exactly (product/techlead/pool/reviewer_count); if they don't, the
  draft is rejected with the conflicting project names rather than picking one.
- **A half-finished `/announce` doesn't survive a restart** — the step-by-step
  composer keeps its answers in memory, so a redeploy mid-compose means starting
  over. The finished draft is a DB row and is unaffected.
- **Stats only cover transitions recorded after the feature shipped** — there's no way
  to backfill reviews that closed before that history table existed.
