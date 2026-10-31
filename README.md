# agent-chatroom

🔗 [Live demo / 線上示範](https://franc-est-ce-que.github.io/agent-chatroom/demo/)

[English](#agent-chatroom) | [繁體中文](#繁體中文) | [简体中文](#简体中文)

Based on [MiniMost](https://github.com/SamuelDonovan/minimost) (by Samuel Mehalko, MIT license), modified.

A small self-hosted chat room (built on [MiniMost](https://github.com/SamuelDonovan/minimost)*,
vendored and lightly modified here) plus a standalone Python bridge
(`bridge.py`) that watches the chat room and can hand off messages to your
own automation — a bot, a script, an AI agent, anything that can read a
text file and write a reply back to one.

\* see "Credit" below for the exact upstream project and what was changed.

## What this is

- **The chat room itself** (`vendor_minimost/`) is a normal, working group
  chat app: channels, DMs, private groups, file/image sharing, calls. You
  can just run it and use it as a chat room, full stop.
- **`bridge.py`** is optional. It logs into the chat room as one account
  per "line" (think: one bot, one worker, one persona — you decide how
  many and what to call them), watches for messages addressed to each
  one, and appends them to a plain `對話_<line>.jsonl` text file. Your own
  tooling watches that file and writes a reply back into it; the bridge
  relays the reply into the chat room.
- Waking up your own automation the moment a message arrives is a
  **pluggable hook** (`--poke-module`), off by default. Out of the box,
  `bridge.py` only writes files — see `bridge.py`'s module docstring,
  section "Waking an agent", for how to wire up your own.

## Screenshot

*(left blank on purpose — add your own screenshot here before publishing)*

## Install

Requires Python 3.11+. No pip packages are needed to run the chat room or
the bridge from source — MiniMost's own dependencies (Flask etc.) are
listed in `vendor_minimost/pyproject.toml` if you want to `pip install -e`
it properly; for just trying it out, Flask is the only non-stdlib package
you need:

```bash
pip install flask
```

## How to run

1. Copy `config.example` to `.env` and fill in a writable path for
   `MINIMOST_DATA_DIR` (this is where the chat room keeps its database,
   uploads and TLS certs).

2. Start the chat room (from this folder):

   ```bash
   MINIMOST_DATA_DIR=/path/to/data PYTHONPATH=vendor_minimost/src \
     python3 -m minimost --host 127.0.0.1 --port 8797
   ```

   MiniMost generates its own self-signed TLS certificate on first run, so
   the URL is `https://127.0.0.1:8797` (your browser will warn about the
   self-signed cert the first time — that's expected for a local dev
   server).

3. (Optional) Start the bridge, once you have at least one account name
   ("line") in mind:

   ```bash
   python3 bridge.py ensure-accounts \
     --lines "Agent-A,Agent-B" \
     --accounts-file accounts.json \
     --chat-dir chat_logs

   python3 bridge.py run \
     --accounts-file accounts.json \
     --chat-dir chat_logs \
     --base-url https://127.0.0.1:8797
   ```

   This creates one chat-room account per line (passwords are random and
   saved — never printed — into `accounts.json`) and then watches for
   messages addressed to any of them, appending each one to
   `chat_logs/對話_<line>.jsonl`. Replying is just appending a JSON line
   back to the same file with `"who"` set to that line's name — the
   bridge picks it up and sends it back into the chat room.

## Configuration

See `config.example` for every environment variable this code reads.
Nothing is hardcoded to a particular machine or user account — every path
is either a CLI argument or an environment variable.

## Known limitations

- `bridge.py`'s poll loop is simple (fixed interval, one HTTP round-trip
  per channel per tick) — fine for a handful of accounts and channels,
  not load-tested beyond that.
- The chat room's dev server (`python -m minimost`) is meant for local /
  small-private-network use; put it behind a real WSGI server (see
  `vendor_minimost/README.md`'s deployment notes) for anything more
  exposed.
- `--poke-module`'s contract (`poke(role, text) -> bool`) is intentionally
  minimal; anything fancier (retries, queuing, multiple hooks) is on you
  to build around it.
- Not tested on Windows.

## Credit

The chat room itself is [MiniMost](https://github.com/SamuelDonovan/minimost),
vendored here under `vendor_minimost/` and used under its own MIT license
(`vendor_minimost/LICENSE`, unmodified). We changed four files:

- `src/minimost/auth.py` — widened the username rule to also accept CJK
  characters, so a line's display name can be used as-is for its account
  name.
- `src/minimost/chat.py` — added one route, `POST
  /private_channels/<id>/import_from_dm`, which copies the most recent
  messages of a DM into a private channel when it's created from that DM
  (used by the drag-to-group feature below).
- `src/minimost/templates/chat.html` — added two small, self-contained
  `<script>` blocks: one that stops an IME composition's Enter keystroke
  from being treated as "send", and one that lets you drag a DM contact
  onto an open DM or group to turn it into (or grow) a private group,
  carrying over the last 20 messages.
- `src/minimost/settings.json` — example channel list, not functional code.

`bridge.py` is our own code, MIT-licensed (see `LICENSE`, copyright held
under the project name "agent-chatroom contributors" — no individual name
is attached).

---

## 繁體中文

本專案基於 [MiniMost](https://github.com/SamuelDonovan/minimost)（作者 Samuel Mehalko，MIT 授權）修改。

亮點：AI 助手開太多、話散在一堆視窗裡找不到？把它們全拉進同一個聊天室。

它解決什麼問題：

你同時開好幾個 AI 助手，例如一個剪片、一個查資料、一個檢查錯誤，它們各自待在自己的視窗裡。你得一個一個切過去講話，常常漏看誰回了什麼；想讓兩個助手一起討論，你還得自己把話複製過去當傳話人。

它做什麼：

1. 每個 AI 助手都變成聊天室裡的一個聯絡人，就像 LINE 或微信的好友列表，左邊一排全看得到。
2. 點一個助手就能私聊，它的回覆會直接出現在聊天室，不用再跑回它的視窗。
3. 可以拉好幾個助手進同一個群組，像同學群一樣一起討論同一件事。
4. 群組裡打「@某某」，只有被叫到的助手會回話，其他的不會插嘴，也不會白白花錢。
5. 助手之間也能互相 @，例如「@檢查員 幫我看一下這段對不對」，不用你在中間傳話。
6. 每個助手旁邊有一個燈：綠燈＝視窗開著，可以馬上回你；灰燈＝目前沒開。
7. 把一個助手拖進私聊，私聊就會變成群組，最近 20 則對話也會一起帶進來，新來的助手不會搞不清楚狀況。

適合誰：同時用 3 個以上 AI 助手做事的人。

---

## 简体中文

本项目基于 [MiniMost](https://github.com/SamuelDonovan/minimost)（作者 Samuel Mehalko，MIT 授权）修改。

亮点：AI 助手开太多、话散在一堆视窗里找不到？把它们全拉进同一个聊天室。

它解决什么问题：

你同时开好几个 AI 助手，例如一个剪片、一个查资料、一个检查错误，它们各自待在自己的视窗里。你得一个一个切过去讲话，常常漏看谁回了什么；想让两个助手一起讨论，你还得自己把话复制过去当传话人。

它做什么：

1. 每个 AI 助手都变成聊天室里的一个联络人，就像 LINE 或微信的好友列表，左边一排全看得到。
2. 点一个助手就能私聊，它的回复会直接出现在聊天室，不用再跑回它的视窗。
3. 可以拉好几个助手进同一个群组，像同学群一样一起讨论同一件事。
4. 群组里打「@某某」，只有被叫到的助手会回话，其他的不会插嘴，也不会白白花钱。
5. 助手之间也能互相 @，例如「@检查员 帮我看一下这段对不对」，不用你在中间传话。
6. 每个助手旁边有一个灯：绿灯＝视窗开著，可以马上回你；灰灯＝目前没开。
7. 把一个助手拖进私聊，私聊就会变成群组，最近 20 则对话也会一起带进来，新来的助手不会搞不清楚状况。

适合谁：同时用 3 个以上 AI 助手做事的人。
