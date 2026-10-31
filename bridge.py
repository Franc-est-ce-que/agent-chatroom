#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bridge.py — MiniMost chat room <-> local "agent" process bridge.

Only uses the Python standard library (urllib / http.cookiejar / ssl /
json) — nothing to pip install.

What it does
------------
1. Makes sure every "line" (a name you pick for one agent / one worker)
   has a MiniMost account. Account usernames are plain ASCII by default,
   but MiniMost's username rule has been relaxed to also accept CJK
   characters, so the account name can be the line's own name.
2. Watches MiniMost for new messages:
   - in a DM, the other participant is always "woken" (see "waking an
     agent" below).
   - in a group, only a line that was actually @mentioned gets woken.
     Mention detection is done locally against the known account list
     (MiniMost's own server-side mention regex is ASCII-only and would
     silently miss any CJK account name).
3. Every time a line should be woken, this script appends one line to
   `對話_<line>.jsonl` (chat log) in --chat-dir, with: which
   group/DM, who spoke, the full original text (messages over 200
   characters get a note saying where to find the full text), and a short
   reminder that any reply should be appended to the same file so the
   bridge can relay it back.
4. Once that file gets a new line written by the line itself, the bridge
   relays it back to MiniMost as a message from that line's account.
5. "Waking an agent" is a plug-in point (see "Waking an agent" below) —
   by default this script only writes the jsonl file above and does NOT
   try to poke, signal, or otherwise notify any external process. Wiring
   it up to actually wake something (run a command, hit a socket, call
   another API) is something you add yourself, outside this script.

Waking an agent (pluggable hook)
---------------------------------
Out of the box, --poke-module is empty and nothing is "poked" — the bridge
only appends to the jsonl files described above, and your own tooling has
to notice the new line on its own (e.g. a file watcher polling the
--chat-dir, or a cron job). This is a deliberate default: this bridge
should never assume what your agent runner looks like.

If you *do* want a direct wake-up call the moment a human message arrives,
write a small Python module with one function:

    def poke(role: str, text: str) -> bool:
        ...  # return True if the wake-up was delivered

and pass its path with --poke-module /path/to/your_hook.py (or set the
CHATROOM_POKE_MODULE environment variable). The bridge lazily imports that
module on the first real human message and calls poke(line_name, preview)
for every wake-up; `role` is the line name, `text` is a short human-
readable summary (not the raw secret-bearing payload).

Identity rules
--------------
A sender counts as a "human" only when their MiniMost account is not one
of the bridge-managed line accounts. For a human message, the jsonl row's
"who" field is the human's own account name (not a fixed literal like
"user") — this matters if your own watcher polls these files and treats
some specific literal string specially, so it doesn't double-fire once
for the bridge's direct poke and once for its own polling. If a line
wants a specific account name shown as "this is really you, the human"
(e.g. you log in under a nickname), list it via the
CHATROOM_HUMAN_ALIASES environment variable (comma-separated).

If the sender is one of the bridge's own line accounts (an echo of its
own reply, or one line talking to another in a group), the "who" field is
that line's own name — never borrowing anyone else's identity. This kind
of row is logged for the record but never counted as "a human spoke",
so it never triggers the poke hook.
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import re
import secrets
import ssl
import string
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# ---------------------------------------------------------------------------
# Account naming: a line's name -> an ASCII-safe username fallback
# (MiniMost's username rule accepts [A-Za-z0-9_-]{1,32} plus CJK characters)
# ---------------------------------------------------------------------------
# Optional cosmetic shortcuts: map a line's display name to a specific
# username, if you want something other than the line's name itself or an
# auto-generated slug. Empty by default — add your own entries here, or
# just use line names that are already valid usernames.
KNOWN_SLUGS: dict[str, str] = {
    # "Agent-A": "agent_a",
}
_USERNAME_RE = re.compile(r"[A-Za-z0-9_\-一-鿿]{1,32}")


def slugify_line(line: str, taken: set) -> str:
    """Turn a line's display name into a valid, not-already-used MiniMost username."""
    base = KNOWN_SLUGS.get(line)
    if base is None:
        if _USERNAME_RE.fullmatch(line):
            base = line
        else:
            import zlib

            base = "line_" + format(zlib.crc32(line.encode("utf-8")) & 0xFFFFFFFF, "x")
    cand = base
    n = 2
    while cand.lower() in taken:
        cand = f"{base}{n}"
        n += 1
    taken.add(cand.lower())
    return cand


def gen_password(n: int = 24) -> str:
    specials = "!@#$%^&*()_+-=[]{};:,.<>?"
    pool = string.ascii_letters + string.digits + specials
    while True:
        pw = "".join(secrets.choice(pool) for _ in range(n))
        if (
            any(c.islower() for c in pw)
            and any(c.isupper() for c in pw)
            and any(c.isdigit() for c in pw)
            and any(c in specials for c in pw)
        ):
            return pw


# ---------------------------------------------------------------------------
# MiniMost HTTP client (stdlib urllib only; manages its own cookie + CSRF)
# ---------------------------------------------------------------------------
_CSRF_RE = re.compile(r'name="csrf_token"\s+value="([^"]*)"')


class MinimostClient:
    def __init__(self, base_url: str, verify_ssl: bool = False, timeout: float = 10):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.cj = http.cookiejar.CookieJar()
        ctx = ssl.create_default_context()
        if not verify_ssl:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cj),
            urllib.request.HTTPSHandler(context=ctx),
        )
        self.username = None

    def _raw(self, method, path, data=None, json_body=None, params=None):
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        headers = {}
        body = None
        if json_body is not None:
            body = json.dumps(json_body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        elif data is not None:
            body = urllib.parse.urlencode(data).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with self.opener.open(req, timeout=self.timeout) as resp:
                return resp.status, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
            # Server not reachable / not up yet: return a fake "network down"
            # status instead of raising, so the caller's loop can keep
            # retrying instead of crashing.
            return 0, f"__NETWORK_DOWN__:{type(e).__name__}:{e}"

    def get_text(self, path, params=None):
        return self._raw("GET", path, params=params)

    def get_json(self, path, params=None):
        status, text = self._raw("GET", path, params=params)
        try:
            return status, json.loads(text)
        except Exception:
            return status, text

    def post_json(self, path, obj):
        return self._raw("POST", path, json_body=obj)

    def _csrf_from(self, path):
        _, html = self.get_text(path)
        m = _CSRF_RE.search(html)
        return m.group(1) if m else ""

    def signup(self, username, password):
        """Returns (ok, detail). ok=True means this call actually created the account."""
        token = self._csrf_from("/signup")
        status, html = self._raw(
            "POST",
            "/signup",
            data={
                "csrf_token": token,
                "username": username,
                "password": password,
                "confirm_password": password,
            },
        )
        # A successful signup redirects to the chat index page, which has
        # id="sidebar"; a failed one re-renders the signup form in place
        # (no such id). The in-chat "change password" mini-form also has
        # autocomplete="current-password", so that can't be used as the marker.
        ok = 'id="sidebar"' in html
        if ok:
            self.username = username
        detail = "created" if ok else ("already exists" if "already exist" in html.lower() else "rejected")
        return ok, detail

    def login(self, username, password):
        token = self._csrf_from("/login")
        status, html = self._raw(
            "POST",
            "/login",
            data={"csrf_token": token, "username": username, "password": password},
        )
        ok = 'id="sidebar"' in html
        if ok:
            self.username = username
        return ok

    # ---- chat API (no CSRF needed, only session cookie) ----
    # If the server is down or returns something unexpected, these all
    # return an empty shell ([] / None) instead of letting the caller loop
    # over garbage.
    def channels(self):
        status, data = self.get_json("/channels")
        return data if status == 200 and isinstance(data, list) else []

    def private_channels(self):
        status, data = self.get_json("/private_channels")
        return data if status == 200 and isinstance(data, list) else []

    def dms(self):
        status, data = self.get_json("/dms")
        return data if status == 200 and isinstance(data, list) else []

    def messages_since(self, channel, after):
        status, data = self.get_json(f"/messages/{urllib.parse.quote(channel, safe=':')}", {"after": after})
        if status != 200 or not isinstance(data, list):
            return []
        return data

    def send(self, channel, text):
        status, text_resp = self._raw(
            "POST",
            f"/send/{urllib.parse.quote(channel, safe=':')}",
            data={"text": text},
        )
        return status, text_resp

    def create_private_channel(self, name, members):
        status, text = self._raw("POST", "/private_channels/create", data=None,
                                  json_body={"name": name, "members": members})
        try:
            return status, json.loads(text)
        except Exception:
            return status, text

    def add_member(self, channel_id, username):
        return self._raw("POST", f"/private_channels/{channel_id}/add_member", json_body={"username": username})

    def import_from_dm(self, channel_id, dm_channel, limit=20):
        status, text = self._raw(
            "POST", f"/private_channels/{channel_id}/import_from_dm",
            json_body={"dm_channel": dm_channel, "limit": limit},
        )
        try:
            return status, json.loads(text)
        except Exception:
            return status, text

    def save_bio(self, bio):
        return self.post_json("/settings", {"bio": bio})

    def is_connected(self):
        """Lightweight probe: is the server reachable right now (no login needed)."""
        status, _ = self._raw("GET", "/login")
        return status not in (0,)


# ---------------------------------------------------------------------------
# @mentions: scanned locally against the known account list (CJK-safe),
# not the server's own precomputed "mentions" field.
# ---------------------------------------------------------------------------
_MENTION_BOUNDARY_CHARS = set("@/_-")


EVERYONE_WORDS = ("@everyone", "@所有人", "@all")


def extract_mentions_local(content, known_usernames):
    """Returns the set of known usernames that "@username" hits inside content.

    Checked longest-name-first so "總框A" can't be mis-matched as a hit for
    "總框"; each hit is then boundary-checked on both sides so "@VPX"
    doesn't count as a hit for "@VP".
    """
    if not content or not known_usernames:
        return set()
    hits = set()
    for uname in sorted(known_usernames, key=len, reverse=True):
        start = 0
        needle = "@" + uname
        while True:
            idx = content.find(needle, start)
            if idx == -1:
                break
            start = idx + 1
            after = idx + len(needle)
            nxt = content[after] if after < len(content) else ""
            if nxt and (nxt.isalnum() or nxt in "_-" or ("一" <= nxt <= "鿿")):
                continue  # e.g. "@VP件" doesn't count as a hit on VP
            prev = content[idx - 1] if idx > 0 else ""
            if prev and prev not in _MENTION_BOUNDARY_CHARS and (prev.isalnum() or ("一" <= prev <= "鿿")):
                continue  # e.g. "x@VP" — treated as a false positive
            hits.add(uname)
    return hits


# ---------------------------------------------------------------------------
# Account registry file (this script's own state, read/write freely)
# ---------------------------------------------------------------------------
def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, obj, mode=0o600):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def ensure_accounts(base_url, verify_ssl, lines, accounts_file):
    """Makes sure every line in `lines` has a MiniMost account (reuses the
    registry file's saved password to re-verify login if it already exists)."""
    accounts = load_json(accounts_file, {})
    taken = {a["username"].lower() for a in accounts.values()}
    report = []
    for line in lines:
        if line in accounts:
            c = MinimostClient(base_url, verify_ssl)
            ok = c.login(accounts[line]["username"], accounts[line]["password"])
            report.append({"line": line, "username": accounts[line]["username"], "status": "login_ok" if ok else "login_FAILED"})
            continue
        username = slugify_line(line, taken)
        password = gen_password()
        c = MinimostClient(base_url, verify_ssl)
        ok, detail = c.signup(username, password)
        if ok:
            c.save_bio(line)
            accounts[line] = {"username": username, "password": password, "bio": line}
            report.append({"line": line, "username": username, "status": "created"})
        else:
            report.append({"line": line, "username": username, "status": f"signup_failed:{detail}"})
    save_json(accounts_file, accounts)
    return accounts, report


# ---------------------------------------------------------------------------
# Waking an agent: a pluggable hook, lazily imported, no-op by default.
# See the "Waking an agent" section of the module docstring.
# ---------------------------------------------------------------------------
DEFAULT_POKE_MODULE = os.environ.get("CHATROOM_POKE_MODULE", "")
HUMAN_ALIASES = {
    a.strip() for a in os.environ.get("CHATROOM_HUMAN_ALIASES", "").split(",") if a.strip()
}


def load_poke_module(path):
    import importlib.util

    spec = importlib.util.spec_from_file_location("chatroom_poke_hook", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def make_poke_fn(path):
    """Returns poke_fn(role, text) -> bool. The hook module is only imported
    on the first call (so commands that never need to poke, like
    ensure-accounts, never touch it)."""
    state = {"mod": None}

    def poke_fn(role, text):
        if state["mod"] is None:
            state["mod"] = load_poke_module(path)
        return state["mod"].poke(role, text)

    return poke_fn


# ---------------------------------------------------------------------------
# jsonl chat-log files (one per line): who/line/page/frame/text/t
# ---------------------------------------------------------------------------
USER_WHO = "使用者"  # literal "the user" — used by --poke-module off setups as a generic marker
OVERFLOW_LIMIT = 200


def chat_file_for(line, chat_dir):
    return os.path.join(chat_dir, f"對話_{line}.jsonl")


def count_lines(path):
    if not os.path.exists(path):
        return 0
    n = 0
    with open(path, "rb") as f:
        for _ in f:
            n += 1
    return n


def append_row(path, row):
    line_no = count_lines(path) + 1
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return line_no


def build_wake_text(path, line_no, kind, group_name, sender_display, original_text):
    """Builds the jsonl row's "text" field: channel info + sender + full
    original text (+ overflow note if long) + reply instructions."""
    overflow_note = ""
    if len(original_text) > OVERFLOW_LIMIT:
        overflow_note = (
            f"（原文共 {len(original_text)} 字，超過預覽長度；"
            f"完整原文在本檔第 {line_no} 行，請讀全文）"
        )
    where = f"群「{group_name}」" if kind == "group" else "私聊"
    reply_target = "同一群" if kind == "group" else "同一私聊"
    if str(sender_display) in HUMAN_ALIASES:
        sender_display = f"使用者本人（聊天室帳號 {sender_display}）"
    header = f"[MiniMost{('群聊' if kind=='group' else '私聊')}] 在{where} 發話人：{sender_display}。"
    body = f"原文：{original_text}"
    tail = f"回話請 append 一行到本檔（who 欄位照你的線名），橋接會把內容貼回{reply_target}。"
    parts = [header]
    if overflow_note:
        parts.append(overflow_note)
    parts.append(body)
    parts.append(tail)
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Main bridge loop
# ---------------------------------------------------------------------------
class Bridge:
    def __init__(self, base_url, verify_ssl, chat_dir, chat_table, accounts, state_file, log_fn=None,
                 poke_fn=None):
        self.base_url = base_url
        self.verify_ssl = verify_ssl
        self.chat_dir = chat_dir
        self.chat_table = chat_table
        self.accounts = accounts  # {line: {username, password, bio}}
        self.username_to_line = {v["username"]: k for k, v in accounts.items()}
        self.fan_usernames = set(self.username_to_line)
        self.state_file = state_file
        # log_fn defaults to flush=True: when run as a long-lived process with
        # stdout redirected to a file, unflushed output would make `tail -f`
        # look dead even though the process is fine.
        self.log = log_fn if log_fn is not None else (lambda m: print(m, flush=True))
        self.state = load_json(state_file, {"channel_cursor": {}, "fan_offset": {}, "fan_last_channel": {}, "fan_last_group_name": {}})
        self.state.setdefault("channel_cursor", {})
        self.state.setdefault("fan_offset", {})
        self.state.setdefault("fan_last_channel", {})
        self.state.setdefault("fan_last_group_name", {})
        self.clients = {}  # line -> MinimostClient (logged in)
        self.wake_counts = {}  # for test visibility: line -> count this run
        self.poke_log = []  # [(ts, role, text, ok)] — test/verification use only
        # A channel's cursor starts at "the moment the bridge started", not
        # "the moment the bridge first noticed this channel" — otherwise a
        # message sent in the few milliseconds between those two moments
        # would be mistaken for "old" and skipped.
        self.start_ts = time.time()
        self.poke_fn = poke_fn  # None by default: write-only, no wake-up call

    def save_state(self):
        save_json(self.state_file, self.state, mode=0o644)

    def register_table(self, line, path):
        if not self.chat_table:
            return
        rows = load_json(self.chat_table, [])
        if not any(r.get("file") == path for r in rows):
            rows.append({"line": line, "file": path})
            save_json(self.chat_table, rows, mode=0o644)

    def login_all(self):
        for line, acc in self.accounts.items():
            c = MinimostClient(self.base_url, self.verify_ssl)
            ok = c.login(acc["username"], acc["password"])
            if not ok:
                self.log(f"[WARN] login failed: {line} ({acc['username']})")
                continue
            self.clients[line] = c
        return self.clients

    def _now_cursor(self, key):
        return self.state["channel_cursor"].get(key, self.start_ts)

    def _wake(self, target_line, kind, channel_id, group_name, sender_username, sender_display, text, msg_ts):
        if target_line not in self.accounts:
            return
        is_human = sender_username not in self.fan_usernames
        if is_human:
            # who = the human's own account name, not a fixed literal — see
            # the module docstring's "Identity rules" for why.
            who = sender_username
        else:
            who = self.username_to_line.get(sender_username, sender_username)
        path = chat_file_for(target_line, self.chat_dir)
        line_no_placeholder = count_lines(path) + 1
        body_text = build_wake_text(path, line_no_placeholder, kind, group_name, sender_display, text)
        row = {
            "who": who,
            "line": target_line,
            "page": None,
            "frame": None,
            "text": body_text,
            "t": time.time(),
            "channel": channel_id,
            "kind": kind,
            "group_name": group_name,
        }
        append_row(path, row)
        self.register_table(target_line, path)
        # Remember which channel this line owes a reply to, so the reply
        # gets relayed back to the right place.
        self.state["fan_last_channel"][target_line] = channel_id
        self.state["fan_last_group_name"][target_line] = group_name
        # This row was written by the bridge itself, not a reply from the
        # line, so its own "already relayed up to here" offset moves forward.
        self.state["fan_offset"][target_line] = count_lines(path)
        self.log(f"[FILE] {target_line} <- {kind}:{channel_id} who={who}")
        if not is_human:
            # One line @-mentioning another in a group also wakes it.
            # Guard against a wake-up loop: the same channel can wake-ping
            # at most 3 times per 5 minutes; a line never wakes itself;
            # only applies in groups (not DMs).
            sender_line = self.username_to_line.get(sender_username, sender_username)
            hist = self.__dict__.setdefault("_fan_poke_hist", {})
            now = time.time()
            recent = [x for x in hist.get(channel_id, []) if now - x < 300]
            if kind != "group" or sender_line == target_line or len(recent) >= 3:
                self.log(f"[INFO-ONLY] {target_line}: line-to-line poke skipped (kind={kind}, same_line={sender_line == target_line}, {len(recent)} already sent in last 5min)")
                return
            recent.append(now); hist[channel_id] = recent
            self.log(f"[FAN-POKE] {sender_line} @ {target_line} (#{len(recent)} in this channel in the last 5min)")
        if self.poke_fn is None:
            return
        poke_text = (
            f"[MiniMost] {target_line} has a new message in "
            f"{('group \"' + group_name + '\"') if kind == 'group' else 'a DM'}, "
            f"from: {sender_display}. Read {path} (last line) for the full text and "
            f"reply by appending a line to that same file (the bridge relays it back "
            f"to the same {'group' if kind=='group' else 'DM'}). "
            f"Preview: \"{text[:120]}\""
        )
        t0 = time.time()
        try:
            ok = bool(self.poke_fn(target_line, poke_text))
        except Exception as e:  # noqa: BLE001
            ok = False
            self.log(f"[POKE-ERROR] {target_line}: {type(e).__name__}: {e}")
        self.poke_log.append({"t": t0, "role": target_line, "ok": ok})
        if ok:
            self.wake_counts[target_line] = self.wake_counts.get(target_line, 0) + 1
        self.log(f"[POKE{'' if ok else '-FAILED'}] {target_line} <- {kind}:{channel_id}")

    def _scan_public_channels(self, scanner_line):
        c = self.clients.get(scanner_line)
        if not c:
            return
        for ch in c.channels():
            key = f"pub:{ch}"
            after = self._now_cursor(key)
            msgs = c.messages_since(ch, after)
            max_ts = after
            for m in msgs:
                max_ts = max(max_ts, m.get("ts", after))
                if m.get("deleted") or m.get("content_type") == "system":
                    continue
                sender = m.get("sender")
                text = m.get("content") or ""
                mentions = extract_mentions_local(text, self.fan_usernames)
                if any(k in text for k in EVERYONE_WORDS):
                    mentions |= set(self.fan_usernames)
                for mu in mentions:
                    target_line = self.username_to_line.get(mu)
                    if not target_line:
                        continue
                    if mu == sender:
                        continue
                    self._wake(target_line, "group", ch, ch, sender, sender, text, m.get("ts"))
            self.state["channel_cursor"][key] = max_ts

    def _scan_private_channels(self):
        """Private group channels: each channel is scanned once per tick,
        no matter how many of our accounts are members (otherwise the
        cursor would get consumed by whichever account scans it first, and
        every other member would never see "new" messages)."""
        channel_client = {}
        channel_meta = {}
        for c in self.clients.values():
            for pc in c.private_channels():
                ch = pc["channel"]
                channel_client.setdefault(ch, c)
                channel_meta[ch] = pc
        for ch, c in channel_client.items():
            key = f"priv:{ch}"
            after = self._now_cursor(key)
            msgs = c.messages_since(ch, after)
            max_ts = after
            group_name = channel_meta[ch].get("name", ch)
            for m in msgs:
                max_ts = max(max_ts, m.get("ts", after))
                if m.get("deleted") or m.get("content_type") == "system":
                    continue
                sender = m.get("sender")
                text = m.get("content") or ""
                mentions = extract_mentions_local(text, self.fan_usernames)
                if any(k in text for k in EVERYONE_WORDS):
                    try:
                        st, mem = c.get_json(f"/private_channels/{channel_meta[ch].get('id')}/members")
                        mentions |= {x.get("username") for x in (mem or []) if x.get("username") in self.fan_usernames}
                    except Exception as e:  # noqa: BLE001
                        self.log(f"[EVERYONE-ERROR] {ch}: {e}")
                for mu in mentions:
                    target_line = self.username_to_line.get(mu)
                    if not target_line or mu == sender:
                        continue
                    self._wake(target_line, "group", ch, group_name, sender, sender, text, m.get("ts"))
            self.state["channel_cursor"][key] = max_ts

    def _scan_group_joins(self):
        """Being added to a private group (however that happened) wakes
        the new member once (so it can catch up), but existing members are
        never re-woken just because someone else joined. The very first
        time a group is seen, its current members are just recorded, not
        woken — only a member added *after* that counts."""
        known = self.state.setdefault("known_group_members", {})
        channel_meta = {}
        for c in self.clients.values():
            for pc in c.private_channels():
                channel_meta[pc["channel"]] = pc
        for ch, pc in channel_meta.items():
            members_now = set(pc.get("members") or [])
            if ch not in known:
                known[ch] = sorted(members_now)
                continue
            prev = set(known[ch])
            for mu in members_now - prev:
                target_line = self.username_to_line.get(mu)
                if target_line:
                    self._wake_group_join(target_line, ch, pc.get("name", ch))
            known[ch] = sorted(members_now)

    def _wake_group_join(self, target_line, channel_id, group_name):
        path = chat_file_for(target_line, self.chat_dir)
        text = (
            f"你被拉進群「{group_name}」。前面最近 20 則訊息已經從原私聊帶過來，"
            f"放在這個頻道歷史最前面，標「（從私聊帶過來）」；請先讀過、瞭解一下情況，"
            f"不用回話，除非被 @ 到。之後照舊：只有被 @ 才需要回。"
        )
        # who = "系統通知" ("system notice"): this row wasn't typed by a
        # human, it was triggered by a membership change — it shouldn't
        # borrow any human's identity.
        row = {
            "who": "系統通知", "line": target_line, "page": None, "frame": None,
            "text": text, "t": time.time(), "channel": channel_id,
            "kind": "group_join", "group_name": group_name,
        }
        append_row(path, row)
        self.register_table(target_line, path)
        self.state["fan_last_channel"][target_line] = channel_id
        self.state["fan_last_group_name"][target_line] = group_name
        self.state["fan_offset"][target_line] = count_lines(path)
        self.log(f"[JOIN] {target_line} added to {channel_id} ({group_name})")
        if self.poke_fn is None:
            return
        try:
            ok = bool(self.poke_fn(target_line, f"[MiniMost] {text}"))
        except Exception as e:  # noqa: BLE001
            ok = False
            self.log(f"[POKE-ERROR] {target_line}: {type(e).__name__}: {e}")
        if ok:
            self.wake_counts[target_line] = self.wake_counts.get(target_line, 0) + 1
        self.log(f"[POKE{'' if ok else '-FAILED'}] new-member poke {target_line} <- {channel_id}")

    def _scan_dms(self):
        """DMs: same idea — each channel scanned once per tick."""
        channel_client = {}
        for c in self.clients.values():
            for dm in c.dms():
                ch = dm["channel"]
                channel_client.setdefault(ch, c)
        for ch, c in channel_client.items():
            key = f"dm:{ch}"
            after = self._now_cursor(key)
            msgs = c.messages_since(ch, after)
            max_ts = after
            participants = ch.split(":")[1:]
            for m in msgs:
                max_ts = max(max_ts, m.get("ts", after))
                if m.get("deleted") or m.get("content_type") == "system":
                    continue
                sender = m.get("sender")
                text = m.get("content") or ""
                for u in participants:
                    target_line = self.username_to_line.get(u)
                    if not target_line or u == sender:
                        continue
                    self._wake(target_line, "dm", ch, "私聊", sender, sender, text, m.get("ts"))
            self.state["channel_cursor"][key] = max_ts

    def _relay_fan_replies(self):
        for line, acc in self.accounts.items():
            path = chat_file_for(line, self.chat_dir)
            if not os.path.exists(path):
                continue
            size_lines = count_lines(path)
            off = self.state["fan_offset"].get(line, 0)
            if size_lines <= off:
                continue
            with open(path, encoding="utf-8") as f:
                all_lines = f.readlines()
            new_lines = all_lines[off:size_lines]
            self.state["fan_offset"][line] = size_lines
            for raw in new_lines:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    row = json.loads(raw)
                except Exception:
                    continue
                # A reply's "who" field may be the line name, the account's
                # current username, or its bio — any of the three counts as
                # "this line replying to itself"; accepting only one of them
                # would silently drop replies after an account rename.
                who = str(row.get("who", ""))
                valid_identities = {line, acc.get("username", ""), acc.get("bio", "")}
                if who not in valid_identities:
                    continue  # not this line's own reply, skip
                target_channel = self.state["fan_last_channel"].get(line)
                if not target_channel:
                    self.log(f"[WARN] {line} replied, but there's no channel on record to relay it to")
                    continue
                c = self.clients.get(line)
                if not c:
                    continue
                status, _ = c.send(target_channel, str(row.get("text", "")))
                self.log(f"[RELAY] {line} reply relayed to {target_channel}: status={status}")

    def tick(self):
        """This must never crash the whole process over a single bad tick
        (can't connect, timeout, server not up yet, any unexpected
        exception) — log one line and let the next tick (after
        poll_interval) try again. No wake-up is missed while disconnected:
        a channel's cursor only advances when messages were actually read;
        on a failed tick messages_since returns an empty list, the cursor
        stays put, and it all catches up once the connection is back."""
        try:
            if not self.clients:
                self.login_all()
            if not self.clients:
                self.log("[DOWN] couldn't log into any account, will retry next tick")
                return
            lines = list(self.accounts.keys())
            if lines:
                self._scan_public_channels(lines[0])
            self._scan_private_channels()
            self._scan_dms()
            self._scan_group_joins()
            self._relay_fan_replies()
            self.save_state()
        except Exception as e:  # noqa: BLE001 — this is the "must not crash" backstop
            self.log(f"[TICK-ERROR] {type(e).__name__}: {e} (ignored, will retry next tick)")

    def run(self, poll_interval=1.0, run_seconds=None):
        start = time.time()
        self.login_all()
        while True:
            self.tick()
            if run_seconds is not None and time.time() - start >= run_seconds:
                break
            time.sleep(poll_interval)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="MiniMost <-> local agent bridge")
    ap.add_argument("cmd", choices=["ensure-accounts", "run"])
    ap.add_argument("--base-url", default=os.environ.get("CHATROOM_BASE_URL", "https://127.0.0.1:8797"))
    ap.add_argument("--chat-dir", default="", help="directory for 對話_<line>.jsonl chat-log files (required)")
    ap.add_argument("--chat-table", default="", help="optional JSON file listing {line, file} pairs, for an external watcher to read")
    ap.add_argument("--accounts-file", default="", help="JSON file this script uses to remember line -> {username, password, bio}")
    ap.add_argument("--state-file", default="", help="JSON file for the bridge's own cursors/offsets (defaults to <chat-dir>/bridge_state.json)")
    ap.add_argument("--lines", default="", help="comma-separated line names; or use --lines-file")
    ap.add_argument("--lines-file", default="", help="a JSON file containing a list of line names")
    ap.add_argument("--poll-interval", type=float, default=1.0)
    ap.add_argument("--run-seconds", type=float, default=None, help="stop after this many seconds (mainly for tests; omit to run forever)")
    ap.add_argument("--poke-module", default=DEFAULT_POKE_MODULE,
                     help="path to a Python file exposing poke(role, text) -> bool; "
                          "empty (the default) means no wake-up call is ever made, "
                          "only the jsonl chat-log files are written")
    args = ap.parse_args()

    if args.lines_file:
        lines = load_json(args.lines_file, [])
    else:
        lines = [x.strip() for x in args.lines.split(",") if x.strip()]

    if args.cmd == "ensure-accounts":
        os.makedirs(args.chat_dir, exist_ok=True)
        accounts, report = ensure_accounts(args.base_url, False, lines, args.accounts_file)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    # run
    os.makedirs(args.chat_dir, exist_ok=True)
    state_file = args.state_file or os.path.join(args.chat_dir, "bridge_state.json")
    accounts = load_json(args.accounts_file, {})
    if lines:
        accounts = {k: v for k, v in accounts.items() if k in lines}
    poke_fn = make_poke_fn(args.poke_module) if args.poke_module else None
    bridge = Bridge(args.base_url, False, args.chat_dir, args.chat_table, accounts, state_file,
                     poke_fn=poke_fn)
    bridge.run(poll_interval=args.poll_interval, run_seconds=args.run_seconds)
    print(json.dumps({"wake_counts": bridge.wake_counts}, ensure_ascii=False))


if __name__ == "__main__":
    main()
