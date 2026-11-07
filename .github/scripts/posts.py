#!/usr/bin/env python3
"""
Keep a full copy of Tinku Thompson's blogs on this site.

For each blog in BLOGS this module:
  1. reads the blog's complete feed (all pages of it),
  2. creates or updates one page per post, using .github/templates/post.html,
  3. deletes pages for posts that were removed from the blog,
  4. rebuilds the blog's index page (all posts by year),
  5. rewrites every link on the site that points to one of these posts so it
     opens the page here instead (and points links to a removed post at the
     blog's index page, so nothing breaks).

A manifest of imported posts is kept in .github/data/posts.json. Posts are only
deleted when the whole feed was read successfully; if a blog can't be reached,
its pages are left exactly as they were.

Normally run by update_feeds.py. To run it on its own:
    python .github/scripts/posts.py                 # live feeds
    python .github/scripts/posts.py --offline DIR   # read DIR/<blog>.xml instead
"""
from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
import pathlib
import posixpath
import re
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, asdict
from urllib.parse import urlparse, parse_qs, unquote

from bs4 import BeautifulSoup, Comment

ROOT = pathlib.Path(__file__).resolve().parents[2]
SITE = "https://gospel-link.com"
OLD_SITES = ("https://gl.joyal.dev",)  # earlier addresses of the site, rewritten to SITE on every run
TEMPLATE = ROOT / ".github" / "templates" / "post.html"
MANIFEST = ROOT / ".github" / "data" / "posts.json"
USER_AGENT = "Mozilla/5.0 (compatible; GospelLinkSiteUpdater/1.0; +https://gospel-link.com)"
PAGE_SIZE = 150

# Blogs to copy onto the site. "feeds" are tried in order; Blogger feeds are read page by page.
BLOGS = {
    "blog": dict(
        name="Spiritual Journey Insights", folder="writing/blog", home="https://tinkuthompson.blogspot.com/",
        hosts=["tinkuthompson.blogspot.com"],
        feeds=["https://tinkuthompson.blogspot.com/feeds/posts/default"]),
    "theologyai": dict(
        name="Theology AI", folder="writing/theology-ai", home="https://www.theologyai.com/",
        hosts=["theologyai.com"],
        feeds=["https://www.theologyai.com/feeds/posts/default", "https://www.theologyai.com/feed"]),
}

NS = {"atom": "http://www.w3.org/2005/Atom", "content": "http://purl.org/rss/1.0/modules/content/",
      "os": "http://a9.com/-/spec/opensearchrss/1.0/"}
log = lambda m: print(m, flush=True)


@dataclass
class Post:
    title: str
    url: str            # address on the original blog
    date: str           # ISO 8601
    path: str = ""      # page on this site, e.g. writing/blog/2026-10-signal-and-noise.html
    summary: str = ""
    labels: list = field(default_factory=list)
    content: str = ""   # full HTML (not stored in the manifest)
    source: str = ""

    @property
    def when(self) -> dt.datetime:
        return dt.datetime.fromisoformat(self.date)


# ------------------------------------------------------------------ reading feeds
def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=40) as r:
        return r.read()


def parse_date(v: str | None) -> dt.datetime | None:
    if not v:
        return None
    import email.utils
    try:
        d = email.utils.parsedate_to_datetime(v.strip())
    except (TypeError, ValueError):
        try:
            d = dt.datetime.fromisoformat(v.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def parse(data: bytes, source: str) -> list[Post]:
    root = ET.fromstring(data)
    out = []
    if root.tag == f"{{{NS['atom']}}}feed":
        for e in root.findall("atom:entry", NS):
            link = next((l.get("href") for l in e.findall("atom:link", NS)
                         if l.get("rel", "alternate") == "alternate" and l.get("type", "text/html") == "text/html"), "")
            d = parse_date(e.findtext("atom:published", None, NS) or e.findtext("atom:updated", None, NS))
            body = e.findtext("atom:content", None, NS) or e.findtext("atom:summary", None, NS) or ""
            labels = [c.get("term") for c in e.findall("atom:category", NS) if c.get("term") and "#kind" not in c.get("term", "")]
            out.append(Post(text(e.findtext("atom:title", "", NS)), link or "", d.isoformat() if d else "", content=body, labels=labels, source=source))
    else:
        for e in root.iter("item"):
            d = parse_date(e.findtext("pubDate"))
            body = e.findtext("content:encoded", None, NS) or e.findtext("description") or ""
            out.append(Post(text(e.findtext("title")), (e.findtext("link") or "").strip(), d.isoformat() if d else "",
                            content=body, labels=[c.text for c in e.findall("category") if c.text], source=source))
    return [p for p in out if p.title and p.url.startswith(("https://", "http://")) and p.date]


def read_blog(key: str, offline: pathlib.Path | None) -> tuple[list[Post] | None, bool]:
    """Return (posts, complete). complete=True means every post on the blog was read."""
    if offline:
        f = offline / f"{key}.xml"
        if not f.exists():
            log(f"  {key}: no offline file")
            return None, False
        try:
            return parse(f.read_bytes(), key), True
        except ET.ParseError as exc:
            log(f"  {key}: could not parse offline file ({exc})")
            return None, False
    for feed in BLOGS[key]["feeds"]:
        try:
            if "/feeds/posts/" in feed:          # Blogger: read every page
                posts, start = [], 1
                while True:
                    sep = "&" if "?" in feed else "?"
                    page = parse(fetch(f"{feed}{sep}max-results={PAGE_SIZE}&start-index={start}"), key)
                    posts += page
                    if len(page) < PAGE_SIZE:
                        break
                    start += PAGE_SIZE
                if posts:
                    log(f"  {key}: {len(posts)} posts from {feed}")
                    return posts, True
            else:                                 # other feeds only list recent posts
                posts = parse(fetch(feed), key)
                if posts:
                    log(f"  {key}: {len(posts)} recent posts from {feed} (not a full list, so nothing will be removed)")
                    return posts, False
        except Exception as exc:
            log(f"  {key}: could not read {feed} ({exc.__class__.__name__}: {exc})")
    return None, False


# ------------------------------------------------------------------ cleaning post HTML
def text(s: str | None) -> str:
    s = html.unescape(re.sub(r"<[^>]+>", " ", s or ""))
    return re.sub(r"\s+", " ", s).strip()


def summarize(body: str, limit: int = 170) -> str:
    t = text(body)
    if len(t) <= limit:
        return t
    return t[:limit].rsplit(" ", 1)[0].rstrip(",;:.") + "…"


def slugify(t: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", t.lower())).strip("-")[:70] or "post"


def youtube(u: str):
    p = urlparse(html.unescape(u.strip()))
    host = p.netloc.lower().removeprefix("www.").removeprefix("m.")
    q = parse_qs(p.query)
    vid = None
    if host == "youtu.be":
        vid = p.path.strip("/").split("/")[0]
    elif host in ("youtube.com", "youtube-nocookie.com"):
        if p.path == "/watch":
            vid = (q.get("v") or [None])[0]
        elif p.path.startswith(("/embed/", "/shorts/", "/live/", "/v/")):
            vid = p.path.split("/")[2].split("&")[0]
            if vid == "videoseries":
                vid = None
        elif p.path != "/playlist":
            return None
    else:
        return None
    lst = (q.get("list") or [None])[0]
    if not (vid or lst) or (vid and not re.fullmatch(r"[\w-]{6,20}", vid)):
        return None
    t = (q.get("t") or q.get("start") or [None])[0]
    start = int(t) if t and t.isdigit() else None
    return vid, lst, start


def video_card(soup, url, caption=""):
    vid, lst, start = youtube(url)
    a = soup.new_tag("a", attrs={"class": "yt" + ("" if vid else " yt-list")})
    if vid:
        a["href"] = f"https://www.youtube.com/watch?v={vid}" + (f"&t={start}s" if start else "")
        a["data-yt"] = vid
        if start:
            a["data-start"] = str(start)
    else:
        a["href"] = f"https://www.youtube.com/playlist?list={lst}"
        a["data-list"] = lst
    thumb = soup.new_tag("span", attrs={"class": "yt-thumb"})
    if not vid:
        thumb.string = "Playlist"
    a.append(thumb)
    a.append(soup.new_tag("span", attrs={"class": "yt-play", "aria-hidden": "true"}))
    sr = soup.new_tag("span", attrs={"class": "sr-only"})
    sr.string = "Play: " + (caption or "video")
    a.append(sr)
    fig = soup.new_tag("figure", attrs={"class": "video"})
    fig.append(a)
    if caption:
        cap = soup.new_tag("figcaption")
        cap.string = caption
        fig.append(cap)
    return fig


def link_card(soup, url, label):
    fig = soup.new_tag("figure", attrs={"class": "video"})
    a = soup.new_tag("a", attrs={"class": "ext-video", "href": url})
    a.string = label
    fig.append(a)
    return fig


BLOCKS = {"p", "div", "ul", "ol", "li", "h2", "h3", "figure", "table", "blockquote", "pre", "hr"}
KEEP = {"a": {"href", "class", "data-yt", "data-list", "data-start", "data-src", "data-keep"},
        "img": {"src", "alt", "loading", "decoding"}, "ol": {"start", "type"}, "td": {"colspan", "rowspan"},
        "th": {"colspan", "rowspan"}, "figure": {"class"}, "span": {"class", "aria-hidden"}, "div": {"class"},
        "h2": {"id"}, "h3": {"id"}, "table": {"class"}, "audio": {"src", "controls", "preload"}}
OWN_CLASSES = {"yt", "yt-list", "yt-thumb", "yt-play", "sr-only", "video", "pic", "ext-video", "video-grid", "table"}


def attached(tag, soup) -> bool:
    """True while the element is still part of the document (not removed or replaced)."""
    return not getattr(tag, "decomposed", False) and any(p is soup for p in tag.parents)


def clean(raw: str) -> str:
    """Turn a blog post's HTML into tidy HTML in the site's style. Keeps all text."""
    raw = re.sub(r"<!--.*?-->", "", raw or "", flags=re.S).replace("\r", "")
    if not re.search(r"<(p|div|br)[\s/>]", raw):
        raw = "".join(f"<p>{p.replace(chr(10), '<br>')}</p>" for p in re.split(r"\n\s*\n", raw.strip()) if p.strip())
    else:
        raw = re.sub(r"\s*\n\s*", " ", raw)
    soup = BeautifulSoup(raw, "html.parser")
    for t in soup(["script", "style", "noscript", "meta", "link"]):
        t.decompose()
    for c in soup.find_all(string=lambda s: isinstance(s, Comment)):
        c.extract()
    for a in soup.find_all("a", attrs={"name": True}):
        if not a.get("href"):
            a.unwrap()

    # embeds (old Blogger posts nest <embed> inside <object>: treat the pair as one video)
    for tag in soup.find_all(["iframe", "embed", "object"]):
        if not attached(tag, soup):
            continue
        holder = tag
        for parent in tag.parents:
            if parent.name == "object":
                holder = parent
        if not attached(holder, soup):
            continue
        src = (holder.get("src") or holder.get("data") or (holder.find("param", attrs={"name": "movie"}) or {}).get("value", "")
               or next((e.get("src") for e in holder.find_all(["embed", "iframe"]) if e.get("src")), "") or tag.get("src") or "")
        if youtube(src or ""):
            holder.replace_with(video_card(soup, src))
        elif src and "facebook.com" in src:
            href = parse_qs(urlparse(html.unescape(src)).query).get("href", [src])[0]
            holder.replace_with(link_card(soup, href, "Watch on Facebook"))
        elif src and (src.split("?")[0].lower().endswith(".swf") or "flash" in (holder.get("type") or tag.get("type") or "")):
            holder.decompose()                          # Flash players no longer work in any browser
        elif src and src.startswith(("http", "//")):
            host = urlparse(src if src.startswith("http") else "https:" + src).netloc.removeprefix("www.")
            holder.replace_with(link_card(soup, src if src.startswith("http") else "https:" + src, f"Open on {host}"))
        else:
            holder.decompose()

    # images (Blogger wraps them in a link to the full-size file, and captions in a table)
    for tbl in soup.select("table.tr-caption-container"):
        img = tbl.find("img")
        cap = tbl.select_one(".tr-caption")
        if img:
            fig = soup.new_tag("figure", attrs={"class": "pic"})
            fig.append(img.extract())
            if cap and cap.get_text(strip=True):
                fc = soup.new_tag("figcaption")
                fc.string = cap.get_text(" ", strip=True)
                fig.append(fc)
            tbl.replace_with(fig)
    for img in soup.find_all("img"):
        if not attached(img, soup):
            continue
        if img.find_parent("figure", class_="pic") and img.parent.name == "figure":
            src = img.get("src", "")
            target = img
        else:
            parent_a = img.find_parent("a")
            target = parent_a if parent_a is not None and not parent_a.get_text(strip=True) else img
            src = img.get("src", "")
        src = html.unescape(src).strip()
        if src.startswith("//"):
            src = "https:" + src
        if not src.startswith(("http://", "https://")):
            target.decompose()
            continue
        src = src.replace("http://", "https://", 1)
        src = re.sub(r"/s\d{2,4}(-[a-z0-9-]+)?/", "/s800/", src) if ("blogspot.com" in src or "googleusercontent.com" in src) else src
        new = soup.new_tag("img", attrs={"src": src, "alt": img.get("alt", ""), "loading": "lazy", "decoding": "async"})
        if target.name == "figure" or (target.parent and target.parent.name == "figure"):
            img.replace_with(new)
        else:
            fig = soup.new_tag("figure", attrs={"class": "pic"})
            fig.append(new)
            target.replace_with(fig)

    # tags
    for t in soup.find_all("b"):
        t.name = "strong"
    for t in soup.find_all("i"):
        t.name = "em"
    for t in soup.find_all("u"):
        if t.find_parent("strong") or t.find("strong"):
            t.unwrap()
        else:
            t.name = "strong"
    for t in soup.find_all("h1"):
        t.name = "h2"
    for t in soup.find_all(["h4", "h5", "h6"]):
        t.name = "h3"
    for t in soup.find_all(["font", "span", "center", "small", "big", "section", "article", "o:p"]):
        if t.name == "span" and set(t.get("class") or []) & OWN_CLASSES:
            continue
        t.unwrap()
    for d in reversed(soup.find_all("div")):
        if set(d.get("class") or []) & OWN_CLASSES:
            continue
        if any(getattr(c, "name", None) in BLOCKS for c in d.children):
            d.unwrap()
        else:
            d.name = "p"
    for p in soup.find_all("p"):
        if p.find(["p", "ul", "ol", "figure", "h2", "h3", "table", "blockquote"]):
            p.unwrap()
    for fig in soup.find_all("figure"):
        if fig.parent and fig.parent.name in ("ul", "ol", "p"):
            fig.parent.insert_before(fig.extract())

    # attributes and links
    for a in soup.find_all("a"):
        href = (a.get("href") or "").strip()
        if not href or href.startswith(("javascript:", "data:")):
            a.unwrap()
            continue
        if href.startswith("//"):
            a["href"] = "https:" + href
    for t in soup.find_all(True):
        keep = KEEP.get(t.name, set())
        for k in list(t.attrs):
            if k not in keep:
                del t[k]
        if t.get("class") and not set(t["class"]) & OWN_CLASSES:
            del t["class"]
    for t in soup.find_all(["p", "li", "h2", "h3", "strong", "em", "blockquote"]):
        if not t.get_text(strip=True).replace("\xa0", "") and not t.find(["img", "figure", "a", "audio"]):
            t.decompose()

    out = str(soup).replace("\xa0", " ").replace("<br/>", "<br>")
    out = re.sub(r"(<br>\s*){2,}", "<br>", out)
    out = re.sub(r"<p>\s*(<br>\s*)+", "<p>", out)
    out = re.sub(r"(\s*<br>)+\s*</p>", "</p>", out)
    out = re.sub(r"<p>\s*</p>", "", out)
    out = re.sub(r"[ \t]{2,}", " ", out)

    soup = BeautifulSoup(out, "html.parser")
    for br in soup.find_all("br"):
        if br.parent is None or br.parent.name == "[document]":
            br.decompose()
    seen = set()
    for h in soup.find_all(["h2", "h3"]):
        hid = slugify(h.get_text(" ", strip=True))
        while hid in seen:
            hid += "-2"
        seen.add(hid)
        h["id"] = hid
    for fig in soup.find_all("figure", class_="video"):          # put runs of videos in a grid
        if fig.parent and "video-grid" in (fig.parent.get("class") or []):
            continue
        grid = soup.new_tag("div", attrs={"class": "video-grid"})
        fig.insert_before(grid)
        node = fig
        while node is not None and getattr(node, "name", None) == "figure" and "video" in (node.get("class") or []):
            nxt = node.find_next_sibling()
            grid.append(node.extract())
            node = nxt if nxt is not None and nxt.previous_sibling is grid else None
    return str(soup).strip()


# ------------------------------------------------------------------ addresses
def norm(u: str) -> tuple[str, str]:
    """Comparable form of a post address (host + path), plus its #fragment."""
    p = urlparse(html.unescape(u.strip()))
    host = p.netloc.lower().removeprefix("www.").removeprefix("m.")
    if re.fullmatch(r"tinkuthompson\.blogspot\.[a-z.]+", host):
        host = "tinkuthompson.blogspot.com"
    path = unquote(p.path).rstrip("/")
    return host + path, p.fragment


def local_path(key: str, post: Post) -> str:
    p = urlparse(post.url)
    m = re.match(r"^/(\d{4})/(\d{2})/([^/]+?)(?:\.html?)?/?$", p.path)
    when = post.when
    slug = f"{m.group(1)}-{m.group(2)}-{slugify(m.group(3))}" if m else f"{when:%Y-%m}-{slugify(p.path.rstrip('/').rsplit('/', 1)[-1] or post.title)}"
    return f"{BLOGS[key]['folder']}/{slug}.html"


def rel(target: str, from_page: str) -> str:
    return posixpath.relpath(target, posixpath.dirname(from_page) or ".")


def fmt_date(iso: str) -> str:
    d = dt.datetime.fromisoformat(iso)
    return f"{d:%B} {d.day}, {d.year}"


# ------------------------------------------------------------------ pages
def render_post(key: str, post: Post, older: Post | None, newer: Post | None, template: str) -> str:
    blog = BLOGS[key]
    esc = lambda s: html.escape(s, quote=True)
    meta = fmt_date(post.date) + (f". Topics: {esc(', '.join(post.labels))}" if post.labels else "")
    nav = ('<nav class="post-foot" aria-label="More posts">'
           + (f'<a href="{rel(older.path, post.path)}">Older: {esc(older.title)}</a>' if older else "<span></span>")
           + (f'<a href="{rel(newer.path, post.path)}">Newer: {esc(newer.title)}</a>' if newer else "")
           + "</nav>")
    original = f'First published on <a data-keep="1" href="{esc(post.url)}">{esc(blog["name"])}</a>.'
    page = template
    for k, v in {"%%TITLE%%": esc(post.title), "%%DESC%%": esc(post.summary), "%%URL%%": f"{SITE}/{post.path}",
                 "#%%BLOG_INDEX%%": "index.html", "%%BLOG_NAME%%": esc(blog["name"]), "%%META%%": meta,
                 "%%ORIGINAL%%": original, "%%NAV%%": nav}.items():
        page = page.replace(k, v)
    return page.replace("%%BODY%%", clean(post.content))


def render_index(key: str, posts: list[Post]) -> str:
    years: dict[str, list[Post]] = {}
    for p in posts:
        years.setdefault(p.date[:4], []).append(p)
    esc = lambda s: html.escape(s, quote=True)
    blocks = []
    for y, ps in years.items():
        items = "".join(f'<li><a href="{posixpath.basename(p.path)}"><span class="t">{esc(p.title)}</span>'
                        f'<span class="src">{fmt_date(p.date)}</span></a></li>' for p in ps)
        blocks.append(f'<div class="year"><h2>{y}</h2><ul>{items}</ul></div>')
    count = f'<p class="muted">{len(posts)} posts. New posts appear here automatically.</p>'
    return "\n      " + count + "\n      " + "\n      ".join(blocks) + "\n      "


def put(page_html: str, key: str, content: str) -> str:
    pat = re.compile(rf"(<!-- feed:{re.escape(key)} -->)(.*?)(<!-- /feed:{re.escape(key)} -->)", re.S)
    return pat.sub(lambda m: m.group(1) + content + m.group(3), page_html)


# ------------------------------------------------------------------ sync
# Pages whose links to blog posts are pointed at the copies here. Anything under writing/
# (the blogs, the Writing page, Selected posts, the archive) plus these individual pages.
# Add a page here if you link to a specific post from it.
LINK_SCOPE = ("writing/", "resources/papers.html")


def in_scope(page: str) -> bool:
    return page.startswith(LINK_SCOPE[0]) or page in LINK_SCOPE[1:]


def sync(offline: pathlib.Path | None = None) -> tuple[dict[str, list[Post]], set[str]]:
    """Create, update and remove post pages. Returns the current posts per blog (newest first) and the pages that changed."""
    template = TEMPLATE.read_text(encoding="utf-8")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else {}
    changed: set[str] = set()

    # Step 1: read every blog first, so links between the two blogs can be resolved
    plan = {}
    for key in BLOGS:
        old = {u: Post(**v) for u, v in manifest.get(key, {}).items()}
        fetched, complete = read_blog(key, offline)
        if fetched is None:
            log(f"  {key}: kept {len(old)} existing pages unchanged")
            plan[key] = (old, old, False, False)
            continue
        current: dict[str, Post] = {}
        for p in fetched:
            p.path = local_path(key, p)
            p.summary = summarize(p.content)
            current[norm(p.url)[0]] = p
        if not complete:                               # recent posts only: keep everything we already have
            for u, p in old.items():
                current.setdefault(u, p)
        used: dict[str, str] = {}                      # two posts with the same file name: number the later one
        for u, p in sorted(current.items(), key=lambda kv: kv[1].date):
            base, n = p.path[:-5], 2
            while p.path in used and used[p.path] != u:
                p.path = f"{base}-{n}.html"
                n += 1
            used[p.path] = u
        plan[key] = (old, current, complete, True)
        manifest[key] = {u: {k: v for k, v in asdict(p).items() if k != "content"} for u, p in current.items()}

    url_map = {u: v["path"] for blog in manifest.values() for u, v in blog.items()}

    # Step 2: write pages, with links already pointing at the copies here
    result: dict[str, list[Post]] = {}
    for key, blog in BLOGS.items():
        old, current, complete, fresh = plan[key]
        ordered = sorted(current.values(), key=lambda p: p.date, reverse=True)
        result[key] = ordered
        if not fresh:
            continue
        added = updated = failed = 0
        for i, p in enumerate(ordered):
            if not p.content:                          # kept from before, not in this feed: leave its page as is
                continue
            newer = ordered[i - 1] if i > 0 else None
            older = ordered[i + 1] if i + 1 < len(ordered) else None
            f = ROOT / p.path
            try:
                page = rewrite_page(p.path, render_post(key, p, older, newer, template), url_map)
            except Exception as exc:
                failed += 1
                log(f"  {key}: could not convert {p.url} ({exc.__class__.__name__}: {exc}); "
                    + ("kept its current page" if f.exists() else "skipped it this time"))
                continue
            if f.exists() and f.read_text(encoding="utf-8") == page:
                continue
            added += not f.exists()
            updated += f.exists()
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(page, encoding="utf-8")
            changed.add(p.path)
        removed = 0
        if complete:
            for u, p in old.items():
                if u not in current and (ROOT / p.path).exists():
                    (ROOT / p.path).unlink()
                    changed.add(p.path)
                    removed += 1
        idx = ROOT / blog["folder"] / "index.html"
        if idx.exists():
            before_idx = idx.read_text(encoding="utf-8")
            after_idx = put(before_idx, f"{key}-index", render_index(key, ordered))
            if after_idx != before_idx:
                idx.write_text(after_idx, encoding="utf-8")
                changed.add(idx.relative_to(ROOT).as_posix())
        log(f"  {key}: {len(ordered)} posts on the site ({added} new, {updated} updated, {removed} removed"
            + (f", {failed} could not be converted" if failed else "") + ")")

    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    new_manifest = json.dumps(manifest, indent=1, ensure_ascii=False)
    if not MANIFEST.exists() or MANIFEST.read_text(encoding="utf-8") != new_manifest:
        MANIFEST.write_text(new_manifest, encoding="utf-8")
    return result, changed


# ------------------------------------------------------------------ links across the site
A_TAG = re.compile(r"<a\s[^>]*>", re.I)


def attr(tag: str, name: str) -> str | None:
    m = re.search(rf'\s{name}="([^"]*)"', tag)
    return html.unescape(m.group(1)) if m else None


def set_attr(tag: str, name: str, value: str | None) -> str:
    tag = re.sub(rf'\s{name}="[^"]*"', "", tag)
    if value is None:
        return tag
    return tag[:-1].rstrip() + f' {name}="{html.escape(value, quote=True)}">'


INDEX_FOR = {h: BLOGS[k]["folder"] + "/index.html" for k in BLOGS for h in BLOGS[k]["hosts"]}


def rewrite_page(name: str, page: str, url_map: dict[str, str]) -> str:
    """Point this page's links to imported posts at their copies here.
    The original address is kept in data-src, so a link to a post that is later
    removed from the blog can be pointed at that blog's index page instead."""
    def fix(m):
        tag = m.group(0)
        if attr(tag, "data-keep"):
            return tag
        href, src = attr(tag, "href"), attr(tag, "data-src")
        original = src or href
        if not original or not original.startswith(("http://", "https://")):
            return tag
        key, frag = norm(original)
        if key in url_map:
            new = rel(url_map[key], name) + (f"#{frag}" if frag else "")
            return set_attr(set_attr(tag, "href", new), "data-src", original)
        if src:                                         # the post was removed from the blog
            fallback = INDEX_FOR.get(key.split("/")[0])
            if fallback and (ROOT / fallback).exists():
                return set_attr(tag, "href", rel(fallback, name))
            return set_attr(set_attr(tag, "href", src), "data-src", None)
        return tag
    return A_TAG.sub(fix, page)


def rewrite_links(pages: dict[str, str]) -> dict[str, str]:
    """Update links on the pages in LINK_SCOPE. Returns only the pages that changed."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else {}
    url_map = {u: v["path"] for blog in manifest.values() for u, v in blog.items()}
    changed = {}
    for name, page in pages.items():
        if not in_scope(name):
            continue
        new_page = rewrite_page(name, page, url_map)
        if new_page != page:
            changed[name] = new_page
    return changed


def write_sitemap(changed: set[str]) -> None:
    """List every public page; refresh the date for pages that changed."""
    sm_path = ROOT / "sitemap.xml"
    old = dict(re.findall(r"<loc>([^<]+)</loc>\s*<lastmod>([^<]+)</lastmod>", sm_path.read_text(encoding="utf-8"))) if sm_path.exists() else {}
    today = dt.date.today().isoformat()
    rows = []
    for f in sorted(ROOT.rglob("*.html")):
        rp = f.relative_to(ROOT).as_posix()
        if rp.startswith(".") or rp == "404.html" or "/_" in rp:
            continue
        if 'name="robots" content="noindex"' in f.read_text(encoding="utf-8")[:4000]:
            continue
        loc = f"{SITE}/" + (rp[: -len("index.html")] if rp.endswith("index.html") else rp)
        lastmod = today if (rp in changed or loc not in old) else old[loc]
        rows.append((0 if rp == "index.html" else 1, rp.count("/"), rp, loc, lastmod))
    rows.sort()
    sm = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + "".join(
        f"  <url>\n    <loc>{loc}</loc>\n    <lastmod>{lm}</lastmod>\n  </url>\n" for *_, loc, lm in rows) + "</urlset>\n"
    if not sm_path.exists() or sm_path.read_text(encoding="utf-8") != sm:
        sm_path.write_text(sm, encoding="utf-8")


if __name__ == "__main__":
    import argparse, sys
    ap = argparse.ArgumentParser(description="Copy blog posts onto the site and update links.")
    ap.add_argument("--offline", type=pathlib.Path, help="read <blog>.xml files from this folder instead of the web")
    args = ap.parse_args()
    log("Syncing blog posts")
    _, synced = sync(args.offline)
    pages = {p.relative_to(ROOT).as_posix(): p.read_text(encoding="utf-8")
             for p in ROOT.rglob("*.html") if not p.relative_to(ROOT).as_posix().startswith(".")}
    changed = rewrite_links(pages)
    for n, h in changed.items():
        (ROOT / n).write_text(h, encoding="utf-8")
    write_sitemap(set(changed) | synced)
    log(f"Links updated on {len(changed)} pages")
    sys.exit(0)
