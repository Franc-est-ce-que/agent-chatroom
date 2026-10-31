# agent-chatroom

🔗 [Live demo / 線上示範](https://franc-est-ce-que.github.io/agent-chatroom/demo/)

[English](#agent-chatroom) | [简体中文](#简体中文)

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

## 繁體中文簡介

這是一個可以自己架的群聊室（底層借用開源專案 MiniMost，我們改了四個檔案，
細節見上面「Credit」），加上一個獨立的 Python 橋接程式 `bridge.py`：橋接
會以「每條線一個帳號」的方式登入聊天室，把收到的訊息寫進一個文字檔
（`對話_<線名>.jsonl`），你自己的程式讀這個檔、把回覆寫回去，橋接就會把
回覆貼回聊天室。

要不要在收到訊息的當下「叫醒」你自己的程式，是一個可插拔的掛鉤
（`--poke-module`），預設關閉——預設只寫檔案，不主動叫醒任何東西；要接自己
的叫醒程式，寫一個 `poke(role, text) -> bool` 函式，用 `--poke-module` 指
給橋接即可，詳細說明在 `bridge.py` 檔頭的「Waking an agent」那一段。

聊天室本身（頻道、私聊、私人群組、檔案與圖片分享、通話）是完整可用的，就
算不接橋接也可以單獨當聊天室用。

---

## 简体中文

[English](#agent-chatroom) | [简体中文](#简体中文)

一个可以自己架设的小型群聊室（底层借用开源项目 [MiniMost](https://github.com/SamuelDonovan/minimost)，收录在本仓库并做了少量修改），外加一个独立的 Python 桥接程序 `bridge.py`：它会监看聊天室，把消息转交给你自己的自动化程序——机器人、脚本、AI agent，任何能读文本文件、再把回复写回去的东西都可以接。

### 这是什么

- **聊天室本身**（`vendor_minimost/`）是一个完整可用的群聊应用：频道、私聊、私人群组、文件/图片分享、通话。不接桥接也可以单独当聊天室用。
- **`bridge.py`** 是可选的。它以“每条线一个账号”的方式登录聊天室（一个机器人、一个worker、一个人设——数量和名字你自己定），监看发给每个账号的消息，写进一个纯文本文件 `對話_<line>.jsonl`。你自己的程序读这个文件、把回复写回去，桥接会把回复转贴回聊天室。
- 收到消息的当下要不要“叫醒”你自己的程序，是一个**可插拔的挂钩**（`--poke-module`），默认关闭。默认情况下 `bridge.py` 只写文件——怎么接自己的叫醒程序，看 `bridge.py` 模块说明里的“Waking an agent”一段。

### 截图

*（故意留空——发布前请自己加截图）*

### 安装

需要 Python 3.11 以上。从源码跑聊天室或桥接不需要装任何 pip 包——MiniMost 自己的依赖（Flask 等）列在 `vendor_minimost/pyproject.toml` 里；只是想试试看的话，只需要装 Flask：

```bash
pip install flask
```

### 怎么运行

1. 把 `config.example` 复制成 `.env`，填一个可写的路径给 `MINIMOST_DATA_DIR`（聊天室的数据库、上传文件、TLS 证书都放在这里）。
2. 启动聊天室（在本文件夹下）：
   ```bash
   MINIMOST_DATA_DIR=/path/to/data PYTHONPATH=vendor_minimost/src \
     python3 -m minimost --host 127.0.0.1 --port 8797
   ```
   MiniMost 第一次运行会自己生成自签名 TLS 证书，所以网址是 `https://127.0.0.1:8797`（浏览器第一次会提示证书不受信任，这是本机开发服务器的正常现象）。
3. （可选）想好至少一个账号名（“line”）之后，启动桥接：
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
   这会给每条线建一个聊天室账号（密码是随机生成的，只存进 `accounts.json`，不会被打印出来），然后监看发给它们的消息，写进 `chat_logs/對話_<line>.jsonl`。回复的方式就是往同一个文件里追加一行 JSON，`"who"` 填那条线自己的名字——桥接会自己捡起来，贴回聊天室。

### 配置

每一个会用到的环境变量都列在 `config.example` 里。没有任何路径写死成某台机器或某个账号——每个路径都是命令行参数或环境变量。

### 已知限制

- `bridge.py` 的轮询循环很简单（固定间隔、每个频道每轮一次 HTTP 往返）——账号和频道数量不多时没问题，没测过更大规模。
- 聊天室自带的开发服务器（`python -m minimost`）是给本机／小型私有网络用的；要给更多人用，请放到正式的 WSGI 服务器后面（见 `vendor_minimost/README.md` 的部署说明）。
- `--poke-module` 的约定（`poke(role, text) -> bool`）故意写得很简单；重试、排队、多个挂钩这些要自己在外面做。
- 没有在 Windows 上测试过。

### 鸣谢

聊天室本身是 [MiniMost](https://github.com/SamuelDonovan/minimost)，收录在 `vendor_minimost/` 下，使用它自己的 MIT 授权（`vendor_minimost/LICENSE`，原文未改）。我们改了四个文件（细节见上面英文版 Credit 一节）：`auth.py`（放宽用户名规则，接受中文字符）、`chat.py`（加一个路由，把私聊最近的消息带进新建的私人群组）、`templates/chat.html`（两段小脚本：不让输入法选字的 Enter 被当成发送、支持把联系人拖进对话建群）、`settings.json`（示例频道列表）。

`bridge.py` 是我们自己写的代码，MIT 授权（见 `LICENSE`，版权挂在项目名「agent-chatroom contributors」下，不挂任何个人名字）。
