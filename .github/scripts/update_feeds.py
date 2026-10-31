#!/usr/bin/env python3
"""
Refresh the parts of the Gospel-Link site that change when new content is published.

Reads the public feeds below and rewrites the HTML between marker comments such as
<!-- feed:newsletter --> ... <!-- /feed:newsletter -->. Uses only the Python standard
library. If a feed can't be reached or parsed, that section is left exactly as it was,
so a bad run never empties the site.

Usage:
    python .github/scripts/update_feeds.py                 # live feeds
    python .github/scripts/update_feeds.py --offline DIR   # read DIR/<name>.xml instead
"""
from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import html
import pathlib
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass

ROOT = pathlib.Path(__file__).resolve().parents[2]
TIMEZONE = "America/Chicago"
USER_AGENT = "Mozilla/5.0 (compatible; GospelLinkSiteUpdater/1.0; +https://gl.joyal.dev)"

FEEDS = {
    "newsletter": ["https://tinkuthompson.substack.com/feed"],
    "blog": [
        "https://tinkuthompson.blogspot.com/feeds/posts/default?alt=rss&max-results=15",
        "https://tinkuthompson.blogspot.com/feeds/posts/default?max-results=15",
    ],
    "theologyai": [
        "https://www.theologyai.com/feeds/posts/default?alt=rss&max-results=15",
        "https://www.theologyai.com/feeds/posts/default?max-results=15",
        "https://www.theologyai.com/feed",
        "https://www.theologyai.com/rss.xml",
    ],
    "podcast": ["https://anchor.fm/s/add0e514/podcast/rss"],
    "jeevan": ["https://anchor.fm/s/ef4c0c44/podcast/rss"],
    "youtube": ["https://www.youtube.com/feeds/videos.xml?channel_id=UCrV-D20f4pD5IQNf_IcRG4A"],
}
SOURCE_LABEL = {"newsletter": "Newsletter", "blog": "Blog", "theologyai": "Theology AI"}
NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "media": "http://search.yahoo.com/mrss/",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd",
}


@dataclass
class Item:
    title: str
    url: str
    date: dt.datetime
    summary: str = ""
    source: str = ""
    video_id: str = ""
    duration: str = ""
    audio: str = ""


# ------------------------------------------------------------------ fetching and parsing
def log(msg: str) -> None:
    print(msg, flush=True)


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def clean_text(raw: str | None, limit: int = 0) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw or "", flags=re.S | re.I)
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    text = re.sub(r"\s+", " ", text).strip()
    if limit and len(text) > limit:
        cut = text[:limit].rsplit(" ", 1)[0].rstrip(",;:.")
        text = cut + "…"
    return text


def parse_date(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    value = value.strip()
    try:
        d = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            d = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def safe_url(u: str | None) -> str:
    u = (u or "").strip()
    return u if u.startswith(("https://", "http://")) else ""


def parse_feed(data: bytes, source: str) -> list[Item]:
    root = ET.fromstring(data)
    items: list[Item] = []
    if root.tag == f"{{{NS['atom']}}}feed":  # Atom: Blogger default, YouTube
        for e in root.findall("atom:entry", NS):
            link = ""
            for l in e.findall("atom:link", NS):
                if l.get("rel", "alternate") == "alternate" and l.get("type", "text/html") == "text/html":
                    link = l.get("href", "")
            date = parse_date(e.findtext("atom:published", None, NS) or e.findtext("atom:updated", None, NS))
            summary = e.findtext("media:group/media:description", None, NS) or e.findtext("atom:summary", None, NS) or e.findtext("atom:content", None, NS)
            items.append(Item(clean_text(e.findtext("atom:title", "", NS)), safe_url(link), date, clean_text(summary, 180),
                              source, (e.findtext("yt:videoId", "", NS) or "").strip()))
    else:  # RSS 2.0: Substack, Blogger ?alt=rss, podcast hosts
        for e in root.iter("item"):
            summary = e.findtext("description") or e.findtext("itunes:summary", None, NS)
            enc = e.find("enclosure")
            items.append(Item(clean_text(e.findtext("title")), safe_url(e.findtext("link")), parse_date(e.findtext("pubDate")),
                              clean_text(summary, 180), source, duration=(e.findtext("itunes:duration", "", NS) or "").strip(),
                              audio=safe_url(enc.get("url") if enc is not None else "")))
    items = [i for i in items if i.title and i.url and i.date]
    items.sort(key=lambda i: i.date, reverse=True)
    return items


def load(name: str, offline: pathlib.Path | None) -> list[Item] | None:
    sources = [offline / f"{name}.xml"] if offline else FEEDS[name]
    for src in sources:
        try:
            data = src.read_bytes() if isinstance(src, pathlib.Path) else fetch(src)
            items = parse_feed(data, name)
            if items:
                log(f"  {name}: {len(items)} items from {src}")
                return items
            log(f"  {name}: no usable items in {src}")
        except Exception as exc:  # network, HTTP or XML errors: try the next source
            log(f"  {name}: could not read {src} ({exc.__class__.__name__}: {exc})")
    log(f"  {name}: keeping the existing content")
    return None


# ------------------------------------------------------------------ rendering
esc = lambda s: html.escape(s, quote=True)


def fmt_date(d: dt.datetime) -> str:
    local = d.astimezone(ZONE)
    return f"{local:%B} {local.day}, {local.year}"


def fmt_duration(raw: str) -> str:
    if not raw:
        return ""
    if raw.isdigit():
        secs = int(raw)
    else:
        parts = [int(p) for p in raw.split(":") if p.isdigit()]
        secs = sum(p * 60 ** i for i, p in enumerate(reversed(parts)))
    mins = round(secs / 60)
    return f"{mins // 60} hr {mins % 60} min" if mins >= 60 else f"{mins} min" if mins else ""


def li_post(i: Item, with_source: bool = False) -> str:
    src = f"{SOURCE_LABEL[i.source]}, {fmt_date(i.date)}" if with_source else fmt_date(i.date)
    return f'<li><a href="{esc(i.url)}"><span class="t">{esc(i.title)}</span><span class="src">{src}</span></a></li>'


def same_story(a: Item, b: Item) -> bool:
    """True when two posts are the same piece shared in two places (e.g. newsletter and blog)."""
    norm = lambda t: re.sub(r"[^\w]", "", t.lower())
    x, y = sorted((norm(a.title), norm(b.title)), key=len)
    return len(x) >= 8 and y.startswith(x)


def render_featured(i: Item) -> str:
    button = "Read the reflection" if i.source == "newsletter" else "Read the post"
    summary = f"\n          <p>{esc(i.summary)}</p>" if i.summary else ""
    return (f'\n          <p class="meta">{SOURCE_LABEL[i.source]}, {fmt_date(i.date)}</p>'
            f'\n          <h3>{esc(i.title)}</h3>{summary}'
            f'\n          <a class="btn btn-pine" href="{esc(i.url)}">{button}</a>\n        ')


def render_videos(items: list[Item], thumbs_dir: pathlib.Path, offline: bool, prefix: str = "") -> str:
    thumbs_dir.mkdir(parents=True, exist_ok=True)
    cards = []
    for n, i in enumerate(items[:6]):
        if not re.fullmatch(r"[\w-]{6,20}", i.video_id or ""):
            continue
        img = thumbs_dir / f"{i.video_id}.webp"
        if not offline and not img.exists():
            try:
                img.write_bytes(fetch(f"https://i.ytimg.com/vi_webp/{i.video_id}/mqdefault.webp"))
            except Exception:
                try:
                    img = img.with_suffix(".jpg")
                    img.write_bytes(fetch(f"https://i.ytimg.com/vi/{i.video_id}/mqdefault.jpg"))
                except Exception as exc:
                    log(f"  youtube: no thumbnail for {i.video_id} ({exc})")
                    img = None
        loading = 'fetchpriority="high"' if n == 0 else 'loading="lazy" decoding="async"'  # first one is on screen at load
        picture = (f'<img src="{prefix}images/yt/{img.name}" width="320" height="180" alt="" {loading}>'
                   if img and img.exists() else "")
        cards.append(f'<li class="video"><a href="https://www.youtube.com/watch?v={i.video_id}">{picture}'
                     f'<h3>{esc(i.title)}</h3><time datetime="{i.date:%Y-%m-%d}">{fmt_date(i.date)}</time></a></li>')
    if not cards:
        return ""
    return f'''
  <section class="block tight" aria-labelledby="latest-videos-title">
    <div class="wrap">
      <div class="head">
        <h2 id="latest-videos-title">Latest videos</h2>
        <a class="text-link" href="https://www.youtube.com/c/GospelLinkM">See all on YouTube</a>
      </div>
      <ul class="videos">
        {(chr(10) + "        ").join(cards)}
      </ul>
    </div>
  </section>
  '''


def render_ep_list(items: list[Item], limit: int | None = None, audio: bool = False) -> str:
    rows = []
    for i in items[:limit]:
        meta = ", ".join(x for x in (fmt_date(i.date), fmt_duration(i.duration)) if x)
        summary = f"<p>{esc(i.summary)}</p>" if i.summary else ""
        player = (f'<audio controls preload="none" src="{esc(i.audio)}"></audio>' if audio and i.audio else "")
        title = f'<a href="{esc(i.url)}">{esc(i.title)}</a>' if i.url else esc(i.title)
        rows.append(f'<li class="ep"><h3>{title}</h3><span class="ep-meta">{meta}</span>{summary}{player}</li>')
    return '<ul class="ep-list">\n        ' + "\n        ".join(rows) + "\n      </ul>"


def render_podcast(items: list[Item], title: str, more_href: str, more_text: str, sid: str, limit: int = 6) -> str:
    return f'''
  <section class="block tight" aria-labelledby="{sid}">
    <div class="wrap">
      <div class="head">
        <h2 id="{sid}">{title}</h2>
        <a class="text-link" href="{more_href}">{more_text}</a>
      </div>
      {render_ep_list(items, limit)}
    </div>
  </section>
  '''


def inject_thumbs(pages: dict[str, str], offline: bool) -> None:
    """Give every YouTube card on the site a locally stored thumbnail."""
    thumbs = ROOT / "images" / "yt"
    thumbs.mkdir(parents=True, exist_ok=True)
    ids = sorted({v for html_ in pages.values() for v in re.findall(r'data-yt="([\w-]{6,20})"', html_)})
    for vid in ids:
        img = thumbs / f"{vid}.webp"
        if offline or img.exists():
            continue
        try:
            img.write_bytes(fetch(f"https://i.ytimg.com/vi_webp/{vid}/mqdefault.webp"))
        except Exception:
            try:
                img.with_suffix(".jpg").write_bytes(fetch(f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg"))
            except Exception as exc:
                log(f"  thumbnails: none for {vid} ({exc.__class__.__name__})")
    for name, page in pages.items():
        prefix = "../" * name.count("/")
        def thumb(m):
            vid = m.group(2)
            for ext in ("webp", "jpg"):
                if (thumbs / f"{vid}.{ext}").exists():
                    return (f'{m.group(1)}<span class="yt-thumb"><img src="{prefix}images/yt/{vid}.{ext}" alt="" width="320" height="180" '
                            f'loading="lazy" decoding="async"></span>')
            return m.group(0)
        pages[name] = re.sub(r'(<a class="yt"[^>]*data-yt="([\w-]+)"[^>]*>)<span class="yt-thumb">(?:<img[^>]*>)?</span>', thumb, page)


# ------------------------------------------------------------------ writing into pages
def put(page: str, key: str, content: str, pages: dict[str, str]) -> None:
    if page not in pages:
        log(f"  warning: {page} not found")
        return
    pattern = re.compile(rf"(<!-- feed:{re.escape(key)} -->)(.*?)(<!-- /feed:{re.escape(key)} -->)", re.S)
    if not pattern.search(pages[page]):
        log(f"  warning: marker feed:{key} not found in {page}")
        return
    pages[page] = pattern.sub(lambda m: m.group(1) + content + m.group(3), pages[page])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--offline", type=pathlib.Path, help="read <name>.xml files from this folder instead of the web")
    args = ap.parse_args()

    names = sorted(p.relative_to(ROOT).as_posix() for p in ROOT.rglob("*.html") if not p.relative_to(ROOT).as_posix().startswith("."))
    pages = {n: (ROOT / n).read_text(encoding="utf-8") for n in names}
    before = dict(pages)

    log("Reading feeds")
    data = {name: load(name, args.offline) for name in FEEDS}

    # Writing page: nine of each
    for key in ("newsletter", "blog", "theologyai"):
        if data[key]:
            put("writing/index.html", key, "\n            " + "\n            ".join(li_post(i) for i in data[key][:9]) + "\n          ", pages)

    # Homepage: newest post overall as the feature, the next four (skipping repeats) as a list
    # Only when all three feeds loaded, so one outage never skews the mix.
    if all(data[k] for k in ("newsletter", "blog", "theologyai")):
        writing = sorted([i for k in ("newsletter", "blog", "theologyai") for i in data[k]], key=lambda i: i.date, reverse=True)
        featured = writing[0]
        put("index.html", "featured", render_featured(featured), pages)
        rest: list[Item] = []
        for i in writing[1:]:
            if not same_story(i, featured) and not any(same_story(i, r) for r in rest):
                rest.append(i)
            if len(rest) == 4:
                break
        put("index.html", "latest", "\n          " + "\n          ".join(li_post(i, True) for i in rest) + "\n        ", pages)
    if data["theologyai"]:
        put("index.html", "theologyai-home", "\n        " + "\n        ".join(
            f'<li><a href="{esc(i.url)}">{esc(i.title)}<small>{fmt_date(i.date)}</small></a></li>' for i in data["theologyai"][:3]) + "\n      ", pages)

    if data["youtube"]:
        block = render_videos(data["youtube"], ROOT / "images" / "yt", bool(args.offline), prefix="../")
        if block:
            put("messages/index.html", "youtube", block, pages)
    if data["podcast"]:
        put("podcast/index.html", "podcast", render_podcast(data["podcast"], "Latest episodes of Thriving Talk", "thriving-talk.html", "All episodes", "episodes-title"), pages)
        put("podcast/thriving-talk.html", "thriving", render_ep_list(data["podcast"], None, audio=True), pages)
    if data["jeevan"]:
        put("podcast/index.html", "jeevan-latest", render_podcast(data["jeevan"], "Latest episodes of Jeevan Unnati Samvaad", "jeevan-unnati-samvaad.html", "All episodes", "jeevan-title", 3), pages)
        put("podcast/jeevan-unnati-samvaad.html", "jeevan", render_ep_list(data["jeevan"], None, audio=True), pages)

    inject_thumbs(pages, bool(args.offline))

    # Footer year on every page
    year = str(dt.datetime.now(ZONE).year)
    for n in pages:
        put(n, "year", year, pages) if "<!-- feed:year -->" in pages[n] else None

    changed = [n for n in pages if pages[n] != before[n]]
    for n in changed:
        (ROOT / n).write_text(pages[n], encoding="utf-8")

    # Sitemap: bump lastmod for pages whose content changed
    sm_path = ROOT / "sitemap.xml"
    if changed and sm_path.exists():
        sm = sm_path.read_text(encoding="utf-8")
        today = dt.datetime.now(ZONE).date().isoformat()
        for n in changed:
            loc = "https://gl.joyal.dev/" + (n[: -len("index.html")] if n.endswith("index.html") else n)
            sm = re.sub(rf"(<loc>{re.escape(loc)}</loc>\s*<lastmod>)[^<]*", rf"\g<1>{today}", sm)
        sm_path.write_text(sm, encoding="utf-8")

    log(f"Updated: {', '.join(changed) if changed else 'nothing changed'}")
    return 0


try:
    from zoneinfo import ZoneInfo
    ZONE = ZoneInfo(TIMEZONE)
except Exception:  # no time zone database available: fall back to UTC
    ZONE = dt.timezone.utc

if __name__ == "__main__":
    sys.exit(main())
