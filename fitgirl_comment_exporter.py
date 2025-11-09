#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Export all Tolstoy comments for a FitGirl page to a standalone HTML file.

v6.2 updates:
- Header toolbar split: left shows Generated + Threads; right shows theme buttons (Dark/Light/System).

Retained:
- Fixed newest → oldest for root threads; replies are oldest → newest.
- Theme switcher with localStorage + live OS tracking.
- Full pagination + per-thread completion using `rootid` when needed.
- Nested replies via <a class="com_ans" data-id="...">.
- Avatars inlined as data: URIs (robust headers + retries).
- Inline embedding of direct-image links (.png/.jpg/.jpeg/.webp/.gif).
- Display name priority: name → nick → "User {id}"

Usage:
  pip install requests
  python tolstoy_comments_to_html.py "https://fitgirl-repacks.site/session-skate-sim/" -o session-skate-sim-comments.html
  # Options: --max-pages 400 --timeout 25 --user-agent "MyUA/1.0"
"""

import argparse
import base64
import datetime as dt
import html
import re
import sys
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlencode, quote

import requests

BASE = "https://web.tolstoycomments.com/api/chatpage"

HEADERS_DEFAULT = {
    "Accept": "application/json, text/plain, */*",
    "Connection": "keep-alive",
    "Referer": "https://fitgirl-repacks.site/",
    "Origin": "https://fitgirl-repacks.site",
    "User-Agent": "Mozilla/5.0 (compatible; TolstoyScraper/6.2; +https://example.local)",
}

IMG_HEADERS = {
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    "Referer": "https://fitgirl-repacks.site/",
    "User-Agent": HEADERS_DEFAULT["User-Agent"],
}

COM_ANS_RE = re.compile(r'data-id="(\d+)"')
HREF_RE = re.compile(r'href="([^"]+)"')
PLAIN_URL_RE = re.compile(r'(https?://[^\s<>"\']+)', re.IGNORECASE)
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif")

# ---------------------- Data models ----------------------

@dataclass
class User:
    id: int
    name: str
    nick: str
    ava: str
    admin: bool
    is_verified: bool
    ava_data_uri: str = ""


@dataclass
class Comment:
    id: int
    text_html: str
    created_iso: str
    user: User
    rating: int
    sort: int
    edited: bool
    fixed: bool
    comment_type: int
    answer_comment_root_id: int
    attaches: list
    parent_id: int = 0                # inferred parent (0 for roots)
    reply_expected: int = 0           # answer_comment_count from payload (only on roots)

# ---------------------- HTTP -----------------------------

def get_json(session: requests.Session, url: str, timeout: int, headers: Dict[str, str]) -> Dict[str, Any]:
    r = session.get(url, timeout=timeout, headers=headers)
    r.raise_for_status()
    return r.json()

def first_page(session: requests.Session, game_url: str, timeout: int, headers: Dict[str, str]) -> Dict[str, Any]:
    params = {"siteid": 6289, "hash": "null", "url": game_url, "sort": 1, "format": 1}
    url = f"{BASE}/first?{urlencode(params, quote_via=quote)}"
    return get_json(session, url, timeout, headers)

def page_call(session: requests.Session, site_id: int, hash_val: str, game_url: str, cursor_sort: int,
              timeout: int, headers: Dict[str, str]) -> Dict[str, Any]:
    params = {"siteid": site_id, "hash": hash_val, "url": game_url, "page": str(cursor_sort), "down": "true", "sort": 1, "format": 1}
    url = f"{BASE}/page?{urlencode(params, quote_via=quote)}"
    return get_json(session, url, timeout, headers)

def thread_call(session: requests.Session, site_id: int, hash_val: str, game_url: str, root_id: int,
                timeout: int, headers: Dict[str, str]) -> Dict[str, Any]:
    params = {"siteid": site_id, "hash": hash_val, "url": game_url, "rootid": root_id, "sort": 1, "format": 1}
    url = f"{BASE}/first?{urlencode(params, quote_via=quote)}"
    return get_json(session, url, timeout, headers)

# ---------------------- Parsing --------------------------

def parse_user(u: Dict[str, Any]) -> User:
    return User(
        id=int(u.get("id", 0)),
        name=str(u.get("name") or ""),
        nick=str(u.get("nick") or ""),
        ava=str(u.get("ava") or ""),
        admin=bool(u.get("admin", False)),
        is_verified=bool(u.get("is_verified", False)),
    )

def parse_comment(c: Dict[str, Any]) -> Comment:
    rating_val = 0
    if isinstance(c.get("raiting"), dict):
        rating_val = int(c["raiting"].get("val", 0))
    reply_expected = int(c.get("answer_comment_count", 0) or 0)
    cm = Comment(
        id=int(c["id"]),
        text_html=str(c.get("text_template") or ""),
        created_iso=str(c.get("data_create") or ""),
        user=parse_user(c.get("user") or {}),
        rating=rating_val,
        sort=int(c.get("sort", 0)),
        edited=bool(c.get("edited", False)),
        fixed=bool(c.get("fixed", False)),
        comment_type=int(c.get("comment_type", 0)),
        answer_comment_root_id=int(c.get("answer_comment_root_id", 0) or 0),
        attaches=c.get("attaches") or [],
        reply_expected=reply_expected,
    )
    m = COM_ANS_RE.search(cm.text_html)  # infer direct parent from anchor
    cm.parent_id = int(m.group(1)) if m else 0
    return cm

def iso_to_local_display(iso_str: str) -> str:
    try:
        dt_aware = dt.datetime.fromisoformat(iso_str)
        local = dt_aware.astimezone()
        return local.strftime("%Y-%m-%d %H:%M:%S %Z")
    except Exception:
        return iso_str

def safe_attr(s: str) -> str:
    return html.escape(s, quote=True)

# ---------------------- Binary embedding -----------------

def url_to_data_uri(session: requests.Session, url: str, timeout: int, max_retries: int = 3) -> Optional[str]:
    delay = 0.25
    for _ in range(max_retries):
        try:
            resp = session.get(url, timeout=timeout, headers=IMG_HEADERS, allow_redirects=True)
            if resp.status_code == 200 and resp.content:
                ctype = (resp.headers.get("Content-Type", "") or "").split(";")[0].strip() or "application/octet-stream"
                b64 = base64.b64encode(resp.content).decode("ascii")
                return f"data:{ctype};base64,{b64}"
        except requests.RequestException:
            pass
        time.sleep(delay); delay *= 2
    return None

def embed_avatars(session: requests.Session, comments: List[Comment], timeout: int) -> None:
    urls: Set[str] = set(c.user.ava for c in comments if c.user.ava)
    cache: Dict[str, Optional[str]] = {}
    for url in urls:
        cache[url] = url_to_data_uri(session, url, timeout)
        time.sleep(0.05)
    for c in comments:
        if c.user.ava and cache.get(c.user.ava):
            c.user.ava_data_uri = cache[c.user.ava] or ""

# ---------------------- Image-link embedding ----------------------

def looks_like_image(url: str) -> bool:
    low = url.split("?")[0].lower()
    return low.endswith(IMAGE_EXTS)

def discover_image_links(c: Comment) -> List[str]:
    urls: Set[str] = set()
    for u in HREF_RE.findall(c.text_html or ""):
        if looks_like_image(u):
            urls.add(u)
    for u in PLAIN_URL_RE.findall(c.text_html or ""):
        if looks_like_image(u):
            urls.add(u)
    for a in c.attaches or []:
        if a.get("type") == "richpreview":
            data = a.get("data") or {}
            u = data.get("url") or ""
            if looks_like_image(u):
                urls.add(u)
    return sorted(urls)

def embed_linked_images(session: requests.Session, comments: List[Comment], timeout: int) -> Dict[int, List[Tuple[str, str]]]:
    by_comment: Dict[int, List[Tuple[str, str]]] = {}
    cache: Dict[str, Optional[str]] = {}
    all_urls: Set[str] = set()
    per_comment_urls: Dict[int, List[str]] = {}
    for c in comments:
        ulist = discover_image_links(c)
        if ulist:
            per_comment_urls[c.id] = ulist
            all_urls.update(ulist)
    for url in all_urls:
        cache[url] = url_to_data_uri(session, url, timeout)
        time.sleep(0.05)
    for cid, ulist in per_comment_urls.items():
        items: List[Tuple[str, str]] = []
        for u in ulist:
            data_uri = cache.get(u)
            if data_uri:
                items.append((u, data_uri))
        if items:
            by_comment[cid] = items
    return by_comment

# ---------------------- Rendering ------------------------

CSS = """
/* ---- Base + dark theme (default) ---- */
:root {
  --bg: #0b0c0f;
  --bg-soft: #12141a;
  --card: #161a22;
  --text: #dde1e7;
  --muted: #9aa3b2;
  --accent: #5ae1ff;
  --accent-2: #8affc1;
  --border: #242b36;
  --chip: #1f2631;
  --good: #6dd27c;
}
:root[data-theme="light"] {
  --bg: #f7f8fa;
  --bg-soft: #ffffff;
  --card: #ffffff;
  --text: #101318;
  --muted: #5a6575;
  --accent: #0b6bcb;
  --accent-2: #0e8e68;
  --border: #dfe3ea;
  --chip: #f2f4f8;
  --good: #1b8e3f;
}
@media (prefers-color-scheme: light) {
  :root:not([data-theme]) {
    --bg: #f7f8fa;
    --bg-soft: #ffffff;
    --card: #ffffff;
    --text: #101318;
    --muted: #5a6575;
    --accent: #0b6bcb;
    --accent-2: #0e8e68;
    --border: #dfe3ea;
    --chip: #f2f4f8;
    --good: #1b8e3f;
  }
}

* { box-sizing: border-box; }
html, body { margin:0; padding:0; background:var(--bg); color:var(--text); font-family: system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol"; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }

.wrap { max-width: 1100px; margin: 32px auto; padding: 0 16px; }
.header { display:flex; gap:14px; align-items:flex-start; flex-wrap:wrap; }
.header h1 { font-size: 1.6rem; margin:0; line-height:1.2; }
.header .sub { color: var(--muted); font-size:0.95rem; }
.header .spacer { flex:1; }

/* New split toolbar */
.toolbar { display:flex; justify-content:space-between; align-items:center; gap:8px; flex-wrap:wrap; width:100%; }
.toolbar-left, .toolbar-right { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
.toolbar-right { margin-left:auto; }
.chip {
  background:var(--chip); border:1px solid var(--border); color:var(--text);
  padding:6px 10px; border-radius:999px; font-size:0.85rem; cursor:default;
}
.theme-chip { cursor:pointer; }
.theme-chip.active { outline: 2px solid var(--accent); }

.cards { margin-top: 18px; display:flex; flex-direction:column; gap:12px; }
.card {
  background: var(--card); border:1px solid var(--border); border-radius:12px;
  padding:14px 14px; display:grid; grid-template-columns:56px 1fr; gap:12px;
}
.ava {
  width:56px; height:56px; border-radius:50%; overflow:hidden; border:1px solid var(--border);
  background: #0b0c0f; display:flex; align-items:center; justify-content:center; font-weight:700; color:#7f8a99;
}
:root[data-theme="light"] .ava { background: #eef2f7; color:#7a8696; }
.ava img { width:100%; height:100%; object-fit:cover; display:block; }
.hdr { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
.name { font-weight:700; }
.badge { font-size:0.75rem; padding:2px 6px; border-radius:6px; border:1px solid var(--border); background:#1a202a; color:#c9d3e1; }
.badge.admin { color:#fff; background:#2c3646; border-color:#364256; }
.badge.verified { color:#0b0c0f; background:#8affc1; border-color:#8affc1; }
:root[data-theme="light"] .badge { background:#f3f5f9; color:#2a343f; }

.time { color:var(--muted); font-size:0.85rem; }
.rating { color: var(--good); font-weight:700; margin-left:auto; }
.body { margin-top:6px; line-height:1.45; }
.body p { margin: 0.5em 0; }

.attach { margin-top:10px; border-left:3px solid var(--border); padding-left:10px; display:flex; gap:10px; align-items:center; }
.attach img { max-height:120px; max-width:200px; border-radius:8px; border:1px solid var(--border); }
.attach video { max-width:320px; border-radius:8px; border:1px solid var(--border); }

.embedded-media { margin-top:10px; display:flex; flex-wrap:wrap; gap:10px; }
.embedded-media figure { margin:0; border:1px solid var(--border); border-radius:8px; padding:8px; background:var(--bg-soft); }
.embedded-media img { max-width:520px; height:auto; display:block; border-radius:6px; }
.embedded-media figcaption { font-size:0.85rem; color:var(--muted); margin-top:6px; }

.thread { margin-top:10px; display:flex; flex-direction:column; gap:8px; }
.reply { display:grid; grid-template-columns:40px 1fr; gap:10px; padding:10px; border:1px dashed var(--border); border-radius:10px; background:var(--bg-soft); }
.reply .ava { width:40px; height:40px; }
.reply .reply { margin-left:40px; } /* nested indent */

.small { font-size:0.85rem; color:var(--muted); }
hr.sep { border:none; height:1px; background:var(--border); margin:18px 0; }
"""

JS = r"""
// --- Theme switcher (Dark/Light/System) ---
(function(){
  const KEY = 'tolstoy_theme'; // values: 'dark' | 'light' | 'system'
  const prefers = window.matchMedia('(prefers-color-scheme: dark)');

  function applyTheme(value) {
    const root = document.documentElement;
    if (value === 'dark') {
      root.setAttribute('data-theme', 'dark');
    } else if (value === 'light') {
      root.setAttribute('data-theme', 'light');
    } else {
      root.removeAttribute('data-theme'); // system
    }
    document.querySelectorAll('[data-theme-btn]').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.themeBtn === value);
    });
  }

  function setTheme(value) {
    localStorage.setItem(KEY, value);
    applyTheme(value);
  }

  function initTheme() {
    const saved = localStorage.getItem(KEY) || 'system';
    applyTheme(saved);
    prefers.addEventListener('change', () => {
      if ((localStorage.getItem(KEY) || 'system') === 'system') {
        applyTheme('system');
      }
    });
    document.querySelectorAll('[data-theme-btn]').forEach(btn => {
      btn.addEventListener('click', () => setTheme(btn.dataset.themeBtn));
    });
  }

  document.addEventListener('DOMContentLoaded', initTheme);
})();
"""

def display_name(u: User) -> str:
    return u.name or u.nick or f"User {u.id}"

def render_badges(u: User) -> str:
    xs = []
    if u.admin: xs.append('<span class="badge admin">Admin</span>')
    if u.is_verified: xs.append('<span class="badge verified">Verified</span>')
    return " ".join(xs)

def render_ava(u: User) -> str:
    src = u.ava_data_uri or (u.ava or "")
    if src:
        return f'<div class="ava"><img src="{safe_attr(src)}" alt=""></div>'
    ini = (u.name or u.nick or "?").strip()[:2].upper()
    return f'<div class="ava">{html.escape(ini)}</div>'

def render_attaches(attaches: list) -> str:
    out = []
    for a in attaches:
        t = a.get("type"); data = a.get("data")
        if t == "richpreview" and isinstance(data, dict):
            url = data.get("url") or ""; host = data.get("host") or ""; thumb = data.get("thumbnail") or ""
            piece = '<div class="attach">'
            if thumb: piece += f'<img src="{safe_attr(thumb)}" alt="">'
            piece += f'<div><div class="small">Link preview • {html.escape(host)}</div><a href="{safe_attr(url)}" target="_blank">{html.escape(url)}</a></div></div>'
            out.append(piece)
        elif t == "giffy" and isinstance(data, list) and data:
            first = data[0]; mp4 = first.get("video") or ""; webp = first.get("webp") or ""; jpg = first.get("src") or ""
            piece = '<div class="attach">'
            if mp4: piece += f'<video controls muted loop src="{safe_attr(mp4)}"></video>'
            elif webp or jpg: piece += f'<img src="{safe_attr(webp or jpg)}" alt="gif">'
            piece += '</div>'
            out.append(piece)
    return "\n".join(out)

def hdr_line(u: User, when: str, rating: int) -> str:
    rate = f'<span class="rating">+{rating}</span>' if rating else ""
    return f'<div class="hdr"><span class="name">{html.escape(display_name(u))}</span> {render_badges(u)} <span class="time">· {html.escape(when)}</span> {rate}</div>'

def render_comment_body(c: Comment) -> str:
    return c.text_html or ""

def render_embedded_images(c: Comment, imgs_by_comment: Dict[int, List[Tuple[str,str]]]) -> str:
    items = imgs_by_comment.get(c.id) or []
    if not items: return ""
    figs = []
    for (orig, data_uri) in items:
        figs.append(f"""
          <figure>
            <img src="{safe_attr(data_uri)}" alt="">
            <figcaption>Embedded image from <a href="{safe_attr(orig)}" target="_blank">{html.escape(orig)}</a></figcaption>
          </figure>
        """)
    return '<div class="embedded-media">' + "\n".join(figs) + "</div>"

def render_reply_tree(node_id: int, children_map: Dict[int, List["Comment"]],
                      imgs_by_comment: Dict[int, List[Tuple[str,str]]]) -> str:
    kids = children_map.get(node_id, [])
    if not kids: return ""
    bits: List[str] = []
    for rc in sorted(kids, key=lambda x: x.sort):  # oldest -> newest within thread
        when = iso_to_local_display(rc.created_iso)
        body = render_comment_body(rc)
        attaches = render_attaches(rc.attaches)
        embedded_imgs = render_embedded_images(rc, imgs_by_comment)
        child_html = render_reply_tree(rc.id, children_map, imgs_by_comment)
        bits.append(f'''
          <div class="reply" id="c{rc.id}">
            {render_ava(rc.user)}
            <div>
              {hdr_line(rc.user, when, rc.rating)}
              <div class="body">{body}</div>
              {attaches}
              {embedded_imgs}
              {child_html}
            </div>
          </div>
        ''')
    return "\n".join(bits)

def render_root(root: Comment, children_map: Dict[int, List[Comment]],
                imgs_by_comment: Dict[int, List[Tuple[str,str]]]) -> str:
    when = iso_to_local_display(root.created_iso)
    body = render_comment_body(root)
    attaches = render_attaches(root.attaches)
    embedded_imgs = render_embedded_images(root, imgs_by_comment)
    anchor = f'<a class="small" href="#c{root.id}" title="Permalink">#{root.id}</a>'
    replies_html = render_reply_tree(root.id, children_map, imgs_by_comment)
    return f"""
    <article class="card" id="c{root.id}">
      {render_ava(root.user)}
      <div>
        {hdr_line(root.user, when, root.rating)} <span style="margin-left:8px">{anchor}</span>
        <div class="body">{body}</div>
        {attaches}
        {embedded_imgs}
        <div class="thread">
          {replies_html}
        </div>
      </div>
    </article>
    """

def build_html(title: str, source_url: str, comments: List[Comment], generated_at_local: str,
               imgs_by_comment: Dict[int, List[Tuple[str,str]]]) -> str:
    # Parent inference & children map
    by_id: Dict[int, Comment] = {c.id: c for c in comments}
    for c in comments:
        if c.answer_comment_root_id == 0:
            c.parent_id = 0
        else:
            if c.parent_id and c.parent_id in by_id:
                pass
            else:
                c.parent_id = c.answer_comment_root_id

    children: Dict[int, List[Comment]] = {}
    roots: List[Comment] = []
    for c in comments:
        if c.answer_comment_root_id == 0:
            roots.append(c)
        else:
            children.setdefault(c.parent_id, []).append(c)

    # FIXED ORDER: newest → oldest for roots
    roots_sorted = sorted(roots, key=lambda x: x.sort, reverse=True)

    items = [render_root(r, children, imgs_by_comment) for r in roots_sorted]
    total_comments = len(comments)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)} – Tolstoy Comments Export</title>
<style>{CSS}</style>
<script>{JS}</script>
</head>
<body>
  <div class="wrap">
    <div class="header" style="width:100%">
      <div>
        <h1>{html.escape(title)}</h1>
        <div class="sub">Standalone export of Tolstoy comments from <a href="{safe_attr(source_url)}" target="_blank">{html.escape(source_url)}</a></div>
      </div>
      <div class="spacer"></div>
      <div class="toolbar">
        <div class="toolbar-left">
          <span class="chip">Generated: {html.escape(generated_at_local)}</span>
          <span class="chip">Threads: <strong>{total_comments}</strong></span>
        </div>
        <div class="toolbar-right">
          <span class="chip theme-chip" data-theme-btn="dark">Dark</span>
          <span class="chip theme-chip" data-theme-btn="light">Light</span>
          <span class="chip theme-chip" data-theme-btn="system">System</span>
        </div>
      </div>
    </div>
    <hr class="sep">
    <section class="cards">
      {"".join(items)}
    </section>
  </div>
</body>
</html>
"""

# ---------------------- Collection ----------------------

def collect_all_comments(game_url: str, timeout: int, headers: Dict[str, str],
                         max_pages: int = 400) -> Tuple[Dict[str, Any], List[Comment], Dict[int, List[Tuple[str,str]]]]:
    with requests.Session() as session:
        # 1) initial batch
        initial = first_page(session, game_url, timeout, headers)
        data = initial.get("data") or {}
        chat = data.get("chat") or {}
        comments_raw = data.get("comments") or []

        site_id = int(chat.get("site_id", 6289))
        hash_val = str(chat.get("hash") or "null")
        title = str(chat.get("title") or "FitGirl Comments")
        total_count = int(chat.get("count_comment_all", 0))

        seen: Set[int] = set()
        comments: List[Comment] = []

        for c in comments_raw:
            cm = parse_comment(c)
            if cm.id not in seen:
                seen.add(cm.id); comments.append(cm)

        # 2) paginate older
        pages_done = 0
        def current_min_sort() -> int:
            return min((c.sort for c in comments), default=0)

        while pages_done < max_pages:
            cursor = current_min_sort()
            if cursor <= 0: break
            resp = page_call(session, site_id, hash_val, game_url, cursor, timeout, headers)
            pages_done += 1
            data_p = resp.get("data") or {}
            new_raw = data_p.get("comments") or []
            if not new_raw: break
            added = 0
            for c in new_raw:
                cm = parse_comment(c)
                if cm.id not in seen:
                    seen.add(cm.id); comments.append(cm); added += 1
            if added == 0: break
            time.sleep(0.12)

        # 3) per-thread completion via rootid
        by_root_count: Dict[int, int] = {}
        for c in comments:
            if c.answer_comment_root_id != 0:
                by_root_count[c.answer_comment_root_id] = by_root_count.get(c.answer_comment_root_id, 0) + 1

        roots = [c for c in comments if c.answer_comment_root_id == 0]
        for root in roots:
            expected = root.reply_expected
            have = by_root_count.get(root.id, 0)
            if expected > have:
                try:
                    tdata = thread_call(session, site_id, hash_val, game_url, root.id, timeout, headers)
                except requests.RequestException:
                    continue
                tcomments_raw = (tdata.get("data") or {}).get("comments") or []
                added = 0
                for tc in tcomments_raw:
                    cm = parse_comment(tc)
                    if cm.id not in seen:
                        seen.add(cm.id); comments.append(cm); added += 1
                if added:
                    by_root_count[root.id] = sum(1 for c in comments if c.answer_comment_root_id == root.id)
                time.sleep(0.08)

        # 4) avatars
        embed_avatars(session, comments, timeout)

        # 5) linked-image embedding
        imgs_by_comment = embed_linked_images(session, comments, timeout)

    meta = {
        "title": title,
        "site_id": site_id,
        "hash": hash_val,
        "total_reported": total_count,
        "pages": pages_done,
    }
    return meta, comments, imgs_by_comment

# ---------------------- CLI -----------------------------

def main():
    ap = argparse.ArgumentParser(description="Export Tolstoy comments to standalone HTML (themes, avatars, images, full threads).")
    ap.add_argument("url", help="FitGirl game page URL (e.g., https://fitgirl-repacks.site/session-skate-sim/)")
    ap.add_argument("-o", "--output", default=None, help="Output HTML filename (default: derived from slug)")
    ap.add_argument("--max-pages", type=int, default=400, help="Safety cap on pagination calls (default 400)")
    ap.add_argument("--timeout", type=int, default=25, help="HTTP timeout in seconds per request (default 25)")
    ap.add_argument("--user-agent", default=None, help="Override default User-Agent")
    args = ap.parse_args()

    headers = dict(HEADERS_DEFAULT)
    if args.user_agent:
        headers["User-Agent"] = args.user_agent

    game_url = args.url.strip()
    if not (game_url.startswith("http://") or game_url.startswith("https://")):
        print("Error: URL must start with http(s)://", file=sys.stderr)
        sys.exit(2)

    try:
        meta, comments, imgs_by_comment = collect_all_comments(game_url, args.timeout, headers, max_pages=args.max_pages)
    except requests.HTTPError as e:
        print(f"HTTP error: {e}", file=sys.stderr); sys.exit(1)
    except requests.RequestException as e:
        print(f"Request error: {e}", file=sys.stderr); sys.exit(1)

    title = meta.get("title") or "FitGirl Comments"
    out_path = args.output or f"{game_url.strip('/').split('/')[-1] or 'fitgirl-comments'}-comments.html"

    generated_local = dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    html_doc = build_html(title, game_url, comments, generated_local, imgs_by_comment)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html_doc)

    roots = sum(1 for c in comments if c.answer_comment_root_id == 0)
    print(f"✅ Wrote {out_path}")
    print(f"   Roots: {roots} • Total comments: {len(comments)} • Reported: {meta.get('total_reported')} • Pages fetched: {meta.get('pages')}")

if __name__ == "__main__":
    main()
