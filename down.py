#!/usr/bin/env python3
"""
Down - Web media downloader
Crawls a URL and downloads images, videos, and documents.
"""
import argparse
import os
import re
import sys
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

warnings.filterwarnings("ignore")

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("[!] Missing dependencies. Run: pip install requests beautifulsoup4")
    sys.exit(1)


# ── File type definitions ─────────────────────────────────────────────────────

EXTENSIONS = {
    "images":    {"jpg", "jpeg", "png", "gif", "webp", "svg", "bmp", "tiff", "ico", "avif"},
    "videos":    {"mp4", "mkv", "avi", "mov", "webm", "flv", "wmv", "m4v", "ts", "mpeg", "mpg"},
    "documents": {"pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "csv", "zip", "rar", "7z", "tar", "gz"},
    "audio":     {"mp3", "wav", "ogg", "flac", "aac", "m4a", "opus", "wma"},
}

ALL_EXTENSIONS = {ext for exts in EXTENSIONS.values() for ext in exts}

FOLDER_MAP = {ext: category for category, exts in EXTENSIONS.items() for ext in exts}

# ── HTTP session ──────────────────────────────────────────────────────────────

def make_session(user_agent=None, referer=None):
    s = requests.Session()
    s.verify = False
    s.headers.update({
        "User-Agent":      user_agent or "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
        "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })
    if referer:
        s.headers["Referer"] = referer
    return s


# ── Crawling ──────────────────────────────────────────────────────────────────

def get_ext(url):
    path = urlparse(url).path
    ext = Path(path).suffix.lstrip(".").lower()
    return ext if ext else None


def crawl(session, url, depth, allowed_types, visited=None):
    if visited is None:
        visited = set()

    if url in visited or depth < 0:
        return set()

    visited.add(url)
    found = set()

    try:
        r = session.get(url, timeout=15)
        if r.status_code != 200:
            return found
        content_type = r.headers.get("Content-Type", "")
        if "html" not in content_type:
            return found
    except Exception:
        return found

    soup = BeautifulSoup(r.text, "html.parser")
    base = "{uri.scheme}://{uri.netloc}".format(uri=urlparse(url))

    tags = [
        ("a",      "href"),
        ("img",    "src"),
        ("video",  "src"),
        ("source", "src"),
        ("link",   "href"),
        ("script", "src"),
        ("embed",  "src"),
    ]

    for tag, attr in tags:
        for el in soup.find_all(tag):
            href = el.get(attr)
            if not href:
                continue
            full = urljoin(url, href)
            ext = get_ext(full)
            if ext and ext in allowed_types:
                found.add(full)
            elif depth > 0 and ext is None and full.startswith(base):
                found |= crawl(session, full, depth - 1, allowed_types, visited)

    return found


# ── Downloading ───────────────────────────────────────────────────────────────

def fmt_size(b):
    for unit in ["B", "KB", "MB", "GB"]:
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} TB"


def safe_filename(url):
    name = Path(urlparse(url).path).name
    name = re.sub(r'[^\w.\-]', '_', name)
    return name or "file"


def download_file(session, url, output_dir, delay=0.0):
    ext = get_ext(url)
    category = FOLDER_MAP.get(ext, "others")
    folder = output_dir / category
    folder.mkdir(parents=True, exist_ok=True)

    filename = safe_filename(url)
    dest = folder / filename

    # avoid overwriting: append suffix if name collision
    counter = 1
    while dest.exists() and dest.stat().st_size > 0:
        stem = Path(filename).stem
        suffix = Path(filename).suffix
        dest = folder / f"{stem}_{counter}{suffix}"
        counter += 1

    if dest.exists() and dest.stat().st_size > 0:
        return "skip", dest, dest.stat().st_size

    try:
        if delay:
            time.sleep(delay)
        r = session.get(url, timeout=30, stream=True)
        if r.status_code != 200:
            return "error", url, f"HTTP {r.status_code}"

        ct = r.headers.get("Content-Type", "")
        if "text/html" in ct:
            return "error", url, "HTML page (not a file)"

        data = b""
        for chunk in r.iter_content(chunk_size=65536):
            data += chunk

        dest.write_bytes(data)
        return "ok", dest, len(data)
    except Exception as ex:
        return "error", url, str(ex)


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(
        prog="down",
        description="Down — crawl a URL and download images, videos and documents",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  down https://example.com
  down https://example.com -o ~/Downloads/site
  down https://example.com -t images,videos
  down https://example.com -d 2 -j 8
  down https://example.com --no-crawl
        """,
    )
    parser.add_argument("url",                        help="Target URL to crawl")
    parser.add_argument("-o", "--output",             default="down_output", help="Output directory (default: ./down_output)")
    parser.add_argument("-t", "--types",              default="all",         help="Types to download: images,videos,documents,audio,all (default: all)")
    parser.add_argument("-d", "--depth",              type=int, default=1,   help="Crawl depth (default: 1)")
    parser.add_argument("-j", "--threads",            type=int, default=4,   help="Concurrent downloads (default: 4)")
    parser.add_argument(      "--delay",              type=float, default=0.2, help="Delay between requests in seconds (default: 0.2)")
    parser.add_argument(      "--ua",                 default=None,          help="Custom User-Agent string")
    parser.add_argument(      "--no-crawl",           action="store_true",   help="Download the URL directly, no crawling")
    parser.add_argument(      "--list",               action="store_true",   help="List found URLs without downloading")
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
    print("  Web media downloader — images, videos, documents")
    print()


def main():
    args = parse_args()
    banner()

    output_dir  = Path(args.output).expanduser().resolve()
    allowed_ext = resolve_types(args.types)
    session     = make_session(user_agent=args.ua, referer=args.url)

    print(f"  Target  : {args.url}")
    print(f"  Output  : {output_dir}")
    print(f"  Types   : {args.types}")
    print(f"  Depth   : {args.depth}")
    print(f"  Threads : {args.threads}")
    print()

    # ── Discover URLs ─────────────────────────────────────────────────────────
    if args.no_crawl:
        ext = get_ext(args.url)
        urls = {args.url} if ext and ext in allowed_ext else set()
        if not urls:
            print(f"[!] URL extension '.{ext}' not in selected types. Use --types or omit --no-crawl.")
            sys.exit(1)
    else:
        print("[*] Crawling...")
        urls = crawl(session, args.url, args.depth, allowed_ext)

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

    results = []
    ok = fail = skip = 0
    total_bytes = 0

    url_list = sorted(urls)
    total = len(url_list)
    width = len(str(total))

    def do_download(i_url):
        i, url = i_url
        status, dest, info = download_file(session, url, output_dir, delay=args.delay)
        return i, url, status, dest, info

    with ThreadPoolExecutor(max_workers=args.threads) as pool:
        futures = {pool.submit(do_download, (i, u)): u for i, u in enumerate(url_list, 1)}
        for fut in as_completed(futures):
            i, url, status, dest, info = fut.result()
            name = Path(dest).name if status != "error" else Path(urlparse(url).path).name

            if status == "ok":
                ok += 1
                total_bytes += info
                label = f"OK   {fmt_size(info):>9}"
                print(f"  [{i:{width}d}/{total}]  {label}  {name}")
            elif status == "skip":
                skip += 1
                total_bytes += info
                label = f"SKIP {fmt_size(info):>9}"
                print(f"  [{i:{width}d}/{total}]  {label}  {name}")
            else:
                fail += 1
                label = f"FAIL          "
                print(f"  [{i:{width}d}/{total}]  {label}  {name}  ({info})")

            results.append((status, dest if status != "error" else url, info))

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

    # ── Folder tree ───────────────────────────────────────────────────────────
    categories = sorted({FOLDER_MAP.get(get_ext(str(p)), "others") for _, p, _ in results if _ != "error"})
    for cat in categories:
        folder = output_dir / cat
        if folder.exists():
            files = sorted(folder.iterdir())
            if files:
                print(f"  {cat}/")
                for f in files:
                    print(f"    {f.name:<55} {fmt_size(f.stat().st_size):>8}")
    print()


if __name__ == "__main__":
    main()
