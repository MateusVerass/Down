#!/usr/bin/env python3
"""
Down - Web media downloader
Just give it a URL — it crawls and downloads everything automatically.
"""
import argparse
import csv
import io
import re
import ssl
import subprocess
import sys
import time
import threading
import urllib.request
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urljoin, urlparse

warnings.filterwarnings("ignore")


# ── Dependency bootstrap ──────────────────────────────────────────────────────

def _pip_install(*packages):
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "-q", *packages],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

def _ensure_deps():
    missing = []
    for pkg in ("requests", "bs4", "curl_cffi"):
        try:
            __import__(pkg)
        except ImportError:
            missing.append({"bs4": "beautifulsoup4"}.get(pkg, pkg))
    if missing:
        print(f"[*] Installing: {', '.join(missing)} ...")
        _pip_install(*missing)


# ── File type definitions ─────────────────────────────────────────────────────

EXTENSIONS = {
    "images": {
        "jpg", "jpeg", "jfif", "jpe",
        "png", "gif", "webp", "apng",
        "raw", "cr2", "cr3", "nef", "nrw", "arw", "srf", "sr2",
        "orf", "rw2", "dng", "pef", "raf", "3fr", "kdc", "dcr",
        "bmp", "tiff", "tif", "ico", "avif", "heic", "heif",
        "psd", "psb", "xcf", "ppm", "pgm", "pbm", "pnm",
        "exr", "hdr", "tga", "wbmp", "xbm", "xpm",
        "svg", "svgz", "ai", "eps",
    },
    "videos": {
        "mp4", "m4v", "mkv", "avi", "mov", "wmv", "flv", "webm",
        "mpeg", "mpg", "mpe", "m1v", "m2v", "mp2", "mpv", "ts",
        "m2ts", "mts", "vob", "mod", "tod",
        "3gp", "3g2", "3gpp", "3gpp2", "f4v", "f4p",
        "ogv", "rm", "rmvb", "asf", "divx", "xvid",
        "mxf", "qt", "yuv", "amv", "nsv", "svi", "trp", "tp",
        "dv", "gxf", "roq",
    },
    "documents": {
        "pdf", "doc", "docx", "odt", "rtf", "pages",
        "xls", "xlsx", "ods", "numbers", "csv", "tsv",
        "ppt", "pptx", "odp", "key",
        "txt", "md", "rst", "tex", "xml", "json", "yaml", "yml",
        "epub", "mobi", "azw", "azw3", "djvu", "fb2", "cbz", "cbr",
        "zip", "rar", "7z", "tar", "gz", "bz2", "xz", "lz4",
        "zst", "cab", "iso", "dmg", "pkg", "deb", "rpm",
    },
    "audio": {
        "mp3", "m4a", "aac", "ogg", "oga", "opus", "flac",
        "wav", "wave", "aiff", "aif", "aifc", "wma", "wv",
        "ape", "mka", "mid", "midi", "amr", "au", "ra",
        "mpc", "tta", "dsd", "dsf", "dff", "caf",
    },
}

ALL_EXTENSIONS = {ext for exts in EXTENSIONS.values() for ext in exts}
FOLDER_MAP     = {ext: cat for cat, exts in EXTENSIONS.items() for ext in exts}

# Pre-built regex for speed — sorted longest first to avoid prefix shadowing
_EXT_PATTERN = re.compile(
    r'["\']((https?:)?//[^"\'<>\s]{4,800}\.('
    + "|".join(sorted(ALL_EXTENSIONS, key=len, reverse=True))
    + r'))["\']',
    re.IGNORECASE,
)

UA_CHROME = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
UA_SAFARI = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.0 Safari/605.1.15"
)

BROWSER_HEADERS = {
    "Accept":                    "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language":           "en-US,en;q=0.9",
    "Cache-Control":             "max-age=0",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest":            "document",
    "Sec-Fetch-Mode":            "navigate",
    "Sec-Fetch-Site":            "none",
    "Sec-Fetch-User":            "?1",
    "sec-ch-ua":                 '"Chromium";v="124","Google Chrome";v="124","Not-A.Brand";v="99"',
    "sec-ch-ua-mobile":          "?0",
    "sec-ch-ua-platform":        '"Windows"',
}

MAX_PAGINATION_PAGES = 200  # safety cap


# ── Sessions ──────────────────────────────────────────────────────────────────

def make_session(referer=None):
    import requests as _requests
    s = _requests.Session()
    s.verify = False
    s.headers.update({"User-Agent": UA_SAFARI, "Accept-Language": "en-US,en;q=0.9"})
    if referer:
        s.headers["Referer"] = referer
    return s


# ── HTML fetching — 4 strategies ─────────────────────────────────────────────

def _is_html(text):
    return bool(text) and "<html" in text[:2000].lower()


def fetch_html(url, session, verbose=False):
    """
    Try 4 strategies until one returns valid HTML:
      1. curl-cffi  — Chrome TLS fingerprint (beats Akamai / Cloudflare)
      2. requests   — fast; blocked on some CDNs by TLS fingerprint
      3. urllib     — different SSL stack; bypasses some blocks
      4. system curl — native binary with full Sec-Fetch headers
    """
    # 1. curl-cffi
    try:
        from curl_cffi import requests as _cffi
        r = _cffi.get(url, impersonate="chrome124",
                      headers={"Accept-Language": "en-US,en;q=0.9"},
                      timeout=20, verify=False)
        if r.status_code == 200 and _is_html(r.text):
            if verbose:
                print("    [html] curl-cffi")
            return r.text
    except Exception:
        pass

    # 2. requests
    try:
        r = session.get(url, headers={**BROWSER_HEADERS, "User-Agent": UA_CHROME}, timeout=15)
        if r.status_code == 200 and _is_html(r.text):
            if verbose:
                print("    [html] requests")
            return r.text
    except Exception:
        pass

    # 3. urllib — no Accept-Encoding to avoid gzip without decompression
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode    = ssl.CERT_NONE
        req = urllib.request.Request(url, headers={
            "User-Agent":      UA_CHROME,
            "Accept":          BROWSER_HEADERS["Accept"],
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control":   "max-age=0",
        })
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            if resp.status == 200:
                html = resp.read().decode("utf-8", errors="replace")
                if _is_html(html):
                    if verbose:
                        print("    [html] urllib")
                    return html
    except Exception:
        pass

    # 4. system curl
    try:
        result = subprocess.run(
            ["curl", "-sL", "--max-time", "20", "-k",
             "-A", UA_CHROME,
             "-H", f"Accept: {BROWSER_HEADERS['Accept']}",
             "-H", "Accept-Language: en-US,en;q=0.9",
             "-H", "Cache-Control: max-age=0",
             "-H", "Sec-Fetch-Dest: document",
             "-H", "Sec-Fetch-Mode: navigate",
             "-H", "Sec-Fetch-Site: none",
             "-H", "Sec-Fetch-User: ?1",
             url],
            capture_output=True, timeout=25,
        )
        html = result.stdout.decode("utf-8", errors="replace")
        if result.returncode == 0 and _is_html(html):
            if verbose:
                print("    [html] curl")
            return html
    except Exception:
        pass

    return None


def fetch_bytes(url, session):
    """Fetch raw bytes, trying curl-cffi first then requests."""
    try:
        from curl_cffi import requests as _cffi
        r = _cffi.get(url, impersonate="chrome124", timeout=30, verify=False)
        if r.status_code == 200:
            return r.content
    except Exception:
        pass
    try:
        r = session.get(url, timeout=30)
        if r.status_code == 200:
            return r.content
    except Exception:
        pass
    return None


# ── URL helpers ───────────────────────────────────────────────────────────────

def get_ext(url):
    path = urlparse(url).path.split("?")[0]
    ext  = Path(path).suffix.lstrip(".").lower()
    return ext if ext else None


def safe_filename(url):
    name = Path(urlparse(url).path.split("?")[0]).name
    name = re.sub(r"[^\w.\-]", "_", name)
    return name or "file"


def auto_output_dir(url):
    p    = urlparse(url)
    host = p.netloc.replace("www.", "")
    path = p.path.strip("/").replace("/", "-")
    name = f"{host}-{path}" if path else host
    return Path(re.sub(r"[^\w.\-]", "_", name))


# ── Data-source resolvers ─────────────────────────────────────────────────────

def resolve_csv(csv_url, base_url, allowed_types, session, verbose=False):
    """Fetch a CSV and extract every media URL from every cell.
    Also resolves DVIDS video IDs found in columns whose header
    contains 'dvids' or 'video id'.
    """
    found = set()
    data  = fetch_bytes(csv_url, session)
    if not data:
        return found

    text   = data.decode("utf-8", errors="replace").lstrip("﻿")
    reader = csv.reader(io.StringIO(text))
    try:
        headers = [h.lower() for h in next(reader)]
    except StopIteration:
        return found

    dvids_cols = {i for i, h in enumerate(headers)
                  if "dvids" in h or "video id" in h or "video_id" in h}

    for row in reader:
        for i, cell in enumerate(row):
            cell = cell.strip()
            if not cell:
                continue

            if cell.startswith("http"):
                ext = get_ext(cell)
                if ext and ext in allowed_types:
                    found.add(cell)

            elif cell.startswith("/"):
                full = urljoin(base_url, cell)
                ext  = get_ext(full)
                if ext and ext in allowed_types:
                    found.add(full)

            elif i in dvids_cols and cell.isdigit():
                mp4 = resolve_dvids(cell, session)
                if mp4:
                    found.add(mp4)
                    if verbose:
                        print(f"    [dvids] {cell} → {mp4}")

    return found


def resolve_dvids(video_id, session):
    """Return the direct .mp4 URL for a DVIDS video ID, or None."""
    html = fetch_html(f"https://www.dvidshub.net/video/{video_id}", session)
    if not html:
        return None
    m = re.search(r'(https?://[^\s"\'<>]+\.mp4[^\s"\'<>]*)', html)
    return m.group(1) if m else None


# ── URL extraction ────────────────────────────────────────────────────────────

def extract_urls(soup, html, base_url, allowed_types, session=None):
    """Extract all downloadable URLs from a BeautifulSoup object + raw HTML."""
    found = set()

    tag_attrs = [
        ("a",      ["href"]),
        ("img",    ["src", "data-src", "data-original", "data-lazy-src"]),
        ("video",  ["src", "poster"]),
        ("source", ["src", "srcset"]),
        ("link",   ["href"]),
        ("embed",  ["src"]),
        ("object", ["data"]),
        ("track",  ["src"]),
    ]

    for tag, attrs in tag_attrs:
        for el in soup.find_all(tag):
            for attr in attrs:
                val = el.get(attr, "") or ""
                # srcset: "url1 1x, url2 2x" — take only the URL part of each entry
                parts = val.split(",")
                for part in parts:
                    tokens = part.strip().split()
                    if not tokens:
                        continue
                    raw = tokens[0]
                    if not raw or raw.startswith("data:"):
                        continue
                    full = urljoin(base_url, raw)
                    ext  = get_ext(full)
                    if ext and ext in allowed_types:
                        found.add(full)

    # Inline style background-image
    for el in soup.find_all(style=True):
        for m in re.finditer(r'url\(["\']?(https?://[^"\')\s]+)["\']?\)', el["style"]):
            full = m.group(1)
            ext  = get_ext(full)
            if ext and ext in allowed_types:
                found.add(full)

    # Raw regex scan of full HTML — catches URLs in JS/JSON blocks
    for m in _EXT_PATTERN.finditer(html):
        raw = m.group(1)
        if raw.startswith("//"):
            raw = "https:" + raw
        ext = get_ext(raw)
        if ext and ext in allowed_types:
            found.add(raw)

    # CSV data sources referenced in JavaScript
    if session:
        csv_regex = re.compile(
            r"""(?:fetch|csvUrl|dataUrl|src)\s*[=(,]\s*["'`]([^"'`\s]{4,400}\.csv(?:\?[^"'`\s]*)?)["'`]""",
            re.IGNORECASE,
        )
        for m in csv_regex.finditer(html):
            csv_url = urljoin(base_url, m.group(1))
            found |= resolve_csv(csv_url, base_url, allowed_types, session)

    return found


# ── Pagination detection ──────────────────────────────────────────────────────

def find_pagination_urls(soup, base_url):
    """Detect all paginated URLs from a BeautifulSoup object."""
    pages = set()

    # Collect all hrefs that already look paginated
    pattern = re.compile(r'[?&](page|p|pg)=(\d+)|/page/(\d+)', re.IGNORECASE)
    paginated_links = []
    for a in soup.find_all("a", href=True):
        href = urljoin(base_url, a["href"])
        if pattern.search(href):
            pages.add(href)
            paginated_links.append(href)

    # Find the highest page number among <a> text nodes (e.g. "1 2 3 … 17")
    page_nums = []
    for a in soup.find_all("a", href=True):
        txt = a.get_text(strip=True)
        if txt.isdigit() and 1 <= int(txt) <= MAX_PAGINATION_PAGES:
            page_nums.append(int(txt))

    if page_nums and paginated_links:
        max_page = max(page_nums)
        template = paginated_links[0]
        m = pattern.search(template)
        if m:
            if m.group(1):  # ?page=N style
                param = m.group(1)
                base_part = re.sub(r'([?&])(' + param + r')=\d+', '', template)
                sep = "&" if "?" in base_part else "?"
                for n in range(1, max_page + 1):
                    pages.add(f"{base_part}{sep}{param}={n}")
            else:           # /page/N style
                base_part = template[:m.start()]
                for n in range(1, max_page + 1):
                    pages.add(f"{base_part}/page/{n}")

    return pages


# ── Crawl ─────────────────────────────────────────────────────────────────────

def crawl(session, url, depth, allowed_types, visited=None, verbose=False):
    if visited is None:
        visited = set()
    if url in visited or depth < 0:
        return set()
    visited.add(url)

    html = fetch_html(url, session, verbose)
    if not html:
        return set()

    from bs4 import BeautifulSoup
    soup  = BeautifulSoup(html, "html.parser")
    found = extract_urls(soup, html, url, allowed_types, session)
    parsed = urlparse(url)
    base   = f"{parsed.scheme}://{parsed.netloc}"

    # Follow pagination (depth=0 so we only grab files, not recurse further)
    for purl in find_pagination_urls(soup, url):
        if purl not in visited:
            found |= crawl(session, purl, 0, allowed_types, visited, verbose)

    # Recurse into same-domain links
    if depth > 0:
        for a in soup.find_all("a", href=True):
            full = urljoin(url, a["href"])
            if get_ext(full) is None and full.startswith(base) and full not in visited:
                found |= crawl(session, full, depth - 1, allowed_types, visited, verbose)

    return found


# ── Downloading ───────────────────────────────────────────────────────────────

def fmt_size(b):
    for unit in ["B", "KB", "MB", "GB"]:
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} TB"


_dl_lock = threading.Lock()


def download_file(session, url, output_dir, delay=0.0, retries=3):
    ext      = get_ext(url)
    category = FOLDER_MAP.get(ext, "others")
    folder   = output_dir / category
    folder.mkdir(parents=True, exist_ok=True)

    filename = safe_filename(url)

    # Thread-safe skip check and destination assignment
    with _dl_lock:
        dest = folder / filename
        if dest.exists() and dest.stat().st_size > 0:
            return "skip", dest, dest.stat().st_size
        # Reserve the destination by touching it (prevents other threads colliding)
        dest.touch()

    if delay:
        time.sleep(delay)

    for attempt in range(retries):
        try:
            r = session.get(url, timeout=60, stream=True)
            if r.status_code != 200:
                if attempt < retries - 1:
                    time.sleep(1)
                    continue
                dest.unlink(missing_ok=True)
                return "error", url, f"HTTP {r.status_code}"

            ct = r.headers.get("Content-Type", "")
            if "text/html" in ct:
                dest.unlink(missing_ok=True)
                return "error", url, "HTML (not a file)"

            # Stream directly to disk — avoids loading large files into RAM
            size = 0
            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    f.write(chunk)
                    size += len(chunk)

            return "ok", dest, size

        except Exception as ex:
            if attempt < retries - 1:
                time.sleep(1)
            else:
                dest.unlink(missing_ok=True)
                return "error", url, str(ex)

    dest.unlink(missing_ok=True)
    return "error", url, "max retries"


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(
        prog="down",
        description="Down — paste a URL, get all media. Fully automatic.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  python3 down.py https://example.com
  python3 down.py https://example.com -o ~/Downloads/site
  python3 down.py https://example.com -t images,videos
  python3 down.py https://example.com -d 2 -j 8
  python3 down.py https://www.war.gov/UFO/
        """,
    )
    parser.add_argument("url",              help="Target URL")
    parser.add_argument("-o", "--output",   default=None,         help="Output directory (default: auto from URL)")
    parser.add_argument("-t", "--types",    default="all",        help="Types: images,videos,documents,audio,all (default: all)")
    parser.add_argument("-d", "--depth",    type=int, default=1,  help="Crawl depth (default: 1)")
    parser.add_argument("-j", "--threads",  type=int, default=8,  help="Concurrent downloads (default: 8)")
    parser.add_argument(      "--delay",    type=float, default=0.1, help="Delay between requests (default: 0.1s)")
    parser.add_argument(      "--no-crawl", action="store_true",  help="Download the URL directly without crawling")
    parser.add_argument(      "--list",     action="store_true",  help="List found URLs without downloading")
    parser.add_argument(      "--html",     default=None,         help="Use a local HTML file (bypass bot protection)")
    parser.add_argument(      "--verbose",  action="store_true",  help="Show which fetch strategy succeeded")
    return parser.parse_args()


def resolve_types(types_arg):
    if types_arg.strip().lower() == "all":
        return ALL_EXTENSIONS
    allowed = set()
    for t in types_arg.split(","):
        t = t.strip().lower()
        if t in EXTENSIONS:
            allowed |= EXTENSIONS[t]
        else:
            print(f"[!] Unknown type '{t}'. Valid: images, videos, documents, audio, all")
    return allowed


def banner():
    print()
    print("  ██████╗  ██████╗ ██╗    ██╗███╗   ██╗")
    print("  ██╔══██╗██╔═══██╗██║    ██║████╗  ██║")
    print("  ██║  ██║██║   ██║██║ █╗ ██║██╔██╗ ██║")
    print("  ██║  ██║██║   ██║██║███╗██║██║╚██╗██║")
    print("  ██████╔╝╚██████╔╝╚███╔███╔╝██║ ╚████║")
    print("  ╚═════╝  ╚═════╝  ╚══╝╚══╝ ╚═╝  ╚═══╝")
    print()
    print("  Web media downloader — images, videos, documents, audio")
    print()


def main():
    _ensure_deps()

    from bs4 import BeautifulSoup  # noqa: F401 — ensure available after deps install

    args        = parse_args()
    output_dir  = Path(args.output).expanduser().resolve() if args.output else auto_output_dir(args.url).resolve()
    allowed_ext = resolve_types(args.types)
    session     = make_session(referer=args.url)

    try:
        from curl_cffi import requests as _  # noqa: F401
        engine = "curl-cffi + requests + urllib + curl"
    except ImportError:
        engine = "requests + urllib + curl"

    banner()
    print(f"  Target  : {args.url}")
    print(f"  Output  : {output_dir}")
    print(f"  Types   : {args.types}  ({len(allowed_ext)} extensions)")
    print(f"  Depth   : {args.depth}")
    print(f"  Threads : {args.threads}")
    print(f"  Engine  : {engine}")
    print()

    # ── Discover URLs ─────────────────────────────────────────────────────────
    if args.no_crawl:
        ext = get_ext(args.url)
        if ext is None or ext not in allowed_ext:
            print(f"[!] '{args.url}' does not have a recognised extension for the selected types.")
            sys.exit(1)
        urls = {args.url}

    elif args.html:
        print(f"[*] Using local HTML: {args.html}")
        from bs4 import BeautifulSoup
        html = Path(args.html).read_text(encoding="utf-8", errors="replace")
        soup = BeautifulSoup(html, "html.parser")
        urls = extract_urls(soup, html, args.url, allowed_ext, session)

    else:
        print("[*] Crawling...")
        urls = crawl(session, args.url, args.depth, allowed_ext, verbose=args.verbose)

    if not urls:
        print("[!] No downloadable files found.")
        sys.exit(0)

    print(f"[+] Found {len(urls)} file(s)")
    print()

    if args.list:
        for u in sorted(urls):
            print(f"  {u}")
        sys.exit(0)

    # ── Download ──────────────────────────────────────────────────────────────
    output_dir.mkdir(parents=True, exist_ok=True)

    results     = []
    ok = fail = skip = 0
    total_bytes = 0
    url_list    = sorted(urls)
    total       = len(url_list)
    width       = len(str(total))

    with ThreadPoolExecutor(max_workers=args.threads) as pool:
        futures = {
            pool.submit(download_file, session, u, output_dir, args.delay): (i, u)
            for i, u in enumerate(url_list, 1)
        }
        for fut in as_completed(futures):
            i, u      = futures[fut]
            status, dest, info = fut.result()
            name = Path(dest).name if status != "error" else Path(urlparse(u).path).name

            if status == "ok":
                ok += 1
                total_bytes += info
                print(f"  [{i:{width}d}/{total}]  OK   {fmt_size(info):>9}  {name}")
            elif status == "skip":
                skip += 1
                total_bytes += info
                print(f"  [{i:{width}d}/{total}]  SKIP {fmt_size(info):>9}  {name}")
            else:
                fail += 1
                print(f"  [{i:{width}d}/{total}]  FAIL            {name}  ({info})")

            results.append((status, dest, info))

    # ── Summary ───────────────────────────────────────────────────────────────
    print()
    print("=" * 60)
    print(f"  Done")
    print(f"  OK   : {ok}")
    print(f"  Skip : {skip}")
    print(f"  Fail : {fail}")
    print(f"  Size : {fmt_size(total_bytes)}")
    print(f"  Dir  : {output_dir}")
    print("=" * 60)
    print()

    cats = sorted({
        FOLDER_MAP.get(get_ext(str(dest)), "others")
        for status, dest, _ in results
        if status != "error"
    })
    for cat in cats:
        folder = output_dir / cat
        if folder.exists():
            files = sorted(folder.iterdir())
            if files:
                print(f"  {cat}/")
                for f in files:
                    if f.stat().st_size > 0:
                        print(f"    {f.name:<55} {fmt_size(f.stat().st_size):>8}")
    print()


if __name__ == "__main__":
    main()
