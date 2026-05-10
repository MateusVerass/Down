#!/usr/bin/env python3
"""
Down - Web media downloader
Crawls a URL and downloads images, videos, and documents.
"""
import argparse
import re
import sys
import time
import urllib.request
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
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
    "images": {
        # JPEG
        "jpg", "jpeg", "jfif", "jpe",
        # PNG / GIF / WebP
        "png", "gif", "webp", "apng",
        # Camera RAW
        "raw", "cr2", "cr3", "nef", "nrw", "arw", "srf", "sr2",
        "orf", "rw2", "dng", "pef", "raf", "3fr", "kdc", "dcr",
        # Other raster
        "bmp", "tiff", "tif", "ico", "avif", "heic", "heif",
        "psd", "psb", "xcf", "ppm", "pgm", "pbm", "pnm",
        "exr", "hdr", "tga", "wbmp", "xbm", "xpm",
        # Vector
        "svg", "svgz", "ai", "eps",
    },
    "videos": {
        # Common
        "mp4", "m4v", "mkv", "avi", "mov", "wmv", "flv", "webm",
        # MPEG
        "mpeg", "mpg", "mpe", "m1v", "m2v", "mp2", "mpv", "ts",
        "m2ts", "mts", "vob", "mod", "tod",
        # Mobile / streaming
        "3gp", "3g2", "3gpp", "3gpp2", "f4v", "f4p",
        # Other
        "ogv", "ogg", "rm", "rmvb", "asf", "divx", "xvid",
        "mxf", "qt", "yuv", "amv", "nsv", "svi", "trp", "tp",
        "dv", "gxf", "roq",
    },
    "documents": {
        # Office
        "pdf", "doc", "docx", "odt", "rtf", "pages",
        "xls", "xlsx", "ods", "numbers", "csv", "tsv",
        "ppt", "pptx", "odp", "key",
        # Text / Markup
        "txt", "md", "rst", "tex", "xml", "json", "yaml", "yml",
        # E-book
        "epub", "mobi", "azw", "azw3", "djvu", "fb2", "cbz", "cbr",
        # Archives
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

# UA for downloading files (Safari — bypasses most CDN blocks)
UA_DOWNLOAD = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
# UA for fetching HTML pages (Windows Chrome — bypasses different CDN blocks)
UA_CRAWL    = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"


# ── HTTP session ──────────────────────────────────────────────────────────────

def make_session(user_agent=None, referer=None):
    s = requests.Session()
    s.verify = False
    s.headers.update({
        "User-Agent":      user_agent or UA_DOWNLOAD,
        "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })
    if referer:
        s.headers["Referer"] = referer
    return s


# ── HTML fetching with fallback ───────────────────────────────────────────────

def fetch_html(session, url):
    """
    Fetch HTML from url using multiple strategies:
    1. requests  — fastest, blocked by some CDNs via TLS fingerprint
    2. urllib    — different TLS stack, bypasses some blocks
    3. curl      — native binary, best browser TLS impersonation
    """
    import subprocess, ssl

    strategies = [
        ("requests", None),
        ("urllib",   None),
        ("curl",     None),
    ]

    for attempt, _ in strategies:
        try:
            if attempt == "requests":
                r = session.get(url, timeout=15)
                if r.status_code != 200:
                    continue
                if "html" not in r.headers.get("Content-Type", ""):
                    continue
                return r.text

            elif attempt == "urllib":
                req = urllib.request.Request(url, headers={
                    "User-Agent":      UA_CRAWL,
                    "Accept":          "text/html,application/xhtml+xml,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept-Encoding": "gzip, deflate",
                    "Cache-Control":   "max-age=0",
                })
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode    = ssl.CERT_NONE
                with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
                    if resp.status != 200:
                        continue
                    if "html" not in resp.headers.get("Content-Type", ""):
                        continue
                    return resp.read().decode("utf-8", errors="replace")

            elif attempt == "curl":
                result = subprocess.run(
                    [
                        "curl", "-sL", "--max-time", "15", "-k",
                        "-A", UA_CRAWL,
                        "-H", "Accept: text/html,application/xhtml+xml,*/*;q=0.8",
                        "-H", "Accept-Language: en-US,en;q=0.9",
                        "-H", "Cache-Control: max-age=0",
                        "-H", "Sec-Fetch-Dest: document",
                        "-H", "Sec-Fetch-Mode: navigate",
                        "-H", "Sec-Fetch-Site: none",
                        "-H", "Sec-Fetch-User: ?1",
                        "-H", "Upgrade-Insecure-Requests: 1",
                        url,
                    ],
                    capture_output=True, timeout=20,
                )
                html = result.stdout.decode("utf-8", errors="replace")
                if result.returncode == 0 and "<html" in html.lower():
                    return html

        except Exception:
            continue

    return None


# ── Crawling ──────────────────────────────────────────────────────────────────

def get_ext(url):
    path = urlparse(url).path.split("?")[0]
    ext  = Path(path).suffix.lstrip(".").lower()
    return ext if ext else None


def extract_urls(html, base_url, allowed_types):
    """Extract all media URLs from an HTML page."""
    soup = BeautifulSoup(html, "html.parser")
    found = set()

    tag_attrs = [
        ("a",      ["href"]),
        ("img",    ["src", "data-src", "data-original", "data-lazy-src"]),
        ("video",  ["src", "poster"]),
        ("source", ["src", "srcset"]),
        ("link",   ["href"]),
        ("embed",  ["src"]),
        ("object", ["data"]),
    ]

    for tag, attrs in tag_attrs:
        for el in soup.find_all(tag):
            for attr in attrs:
                val = el.get(attr, "")
                if not val:
                    continue
                # srcset can have multiple URLs: "url1 1x, url2 2x"
                candidates = [v.strip().split()[0] for v in val.split(",")]
                for raw in candidates:
                    if not raw or raw.startswith("data:"):
                        continue
                    full = urljoin(base_url, raw)
                    ext  = get_ext(full)
                    if ext and ext in allowed_types:
                        found.add(full)

    # Also scan inline style attributes for background-image URLs
    for el in soup.find_all(style=True):
        for m in re.finditer(r'url\(["\']?(https?://[^"\')\s]+)["\']?\)', el["style"]):
            full = m.group(1)
            ext  = get_ext(full)
            if ext and ext in allowed_types:
                found.add(full)

    return found


def crawl(session, url, depth, allowed_types, visited=None):
    if visited is None:
        visited = set()

    if url in visited or depth < 0:
        return set()

    visited.add(url)

    html = fetch_html(session, url)
    if not html:
        return set()

    found    = extract_urls(html, url, allowed_types)
    base     = "{uri.scheme}://{uri.netloc}".format(uri=urlparse(url))

    if depth > 0:
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            full = urljoin(url, a["href"])
            ext  = get_ext(full)
            if ext is None and full.startswith(base) and full not in visited:
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
    name = Path(urlparse(url).path.split("?")[0]).name
    name = re.sub(r"[^\w.\-]", "_", name)
    return name or "file"


def download_file(session, url, output_dir, delay=0.0):
    ext      = get_ext(url)
    category = FOLDER_MAP.get(ext, "others")
    folder   = output_dir / category
    folder.mkdir(parents=True, exist_ok=True)

    filename = safe_filename(url)
    dest     = folder / filename

    counter = 1
    while dest.exists() and dest.stat().st_size > 0:
        stem   = Path(filename).stem
        suffix = Path(filename).suffix
        dest   = folder / f"{stem}_{counter}{suffix}"
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
  down https://www.war.gov/UFO/ -t images,documents
  down https://example.com --no-crawl
        """,
    )
    parser.add_argument("url",               help="Target URL to crawl")
    parser.add_argument("-o", "--output",    default="down_output",  help="Output directory (default: ./down_output)")
    parser.add_argument("-t", "--types",     default="all",          help="Types: images,videos,documents,audio,all (default: all)")
    parser.add_argument("-d", "--depth",     type=int, default=1,    help="Crawl depth (default: 1)")
    parser.add_argument("-j", "--threads",   type=int, default=4,    help="Concurrent downloads (default: 4)")
    parser.add_argument(      "--delay",     type=float, default=0.2, help="Delay between requests in seconds (default: 0.2)")
    parser.add_argument(      "--ua",        default=None,           help="Custom User-Agent string")
    parser.add_argument(      "--no-crawl",  action="store_true",    help="Download the URL directly, no crawling")
    parser.add_argument(      "--list",      action="store_true",    help="List found URLs without downloading")
    parser.add_argument(      "--html",      default=None,           help="Use a local HTML file instead of fetching the URL (bypass bot protection)")
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
        ext  = get_ext(args.url)
        urls = {args.url} if ext and ext in allowed_ext else set()
        if not urls:
            print(f"[!] URL extension '.{ext}' not in selected types.")
            sys.exit(1)
    elif args.html:
        print(f"[*] Using local HTML: {args.html}")
        html = Path(args.html).read_text(encoding="utf-8", errors="replace")
        urls = extract_urls(html, args.url, allowed_ext)
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

    results    = []
    ok = fail = skip = 0
    total_bytes = 0
    url_list   = sorted(urls)
    total      = len(url_list)
    width      = len(str(total))

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
                print(f"  [{i:{width}d}/{total}]  OK   {fmt_size(info):>9}  {name}")
            elif status == "skip":
                skip += 1
                total_bytes += info
                print(f"  [{i:{width}d}/{total}]  SKIP {fmt_size(info):>9}  {name}")
            else:
                fail += 1
                print(f"  [{i:{width}d}/{total}]  FAIL            {name}  ({info})")

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
    cats = sorted({FOLDER_MAP.get(get_ext(str(p)), "others") for s, p, _ in results if s != "error"})
    for cat in cats:
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
