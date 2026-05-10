#!/usr/bin/env python3
"""
Down - Web media downloader
Just give it a URL — it crawls and downloads everything automatically.
"""
import argparse
import re
import subprocess
import sys
import time
import urllib.request
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urljoin, urlparse

warnings.filterwarnings("ignore")


# ── Auto-install dependencies ─────────────────────────────────────────────────

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

_ensure_deps()

import requests                          # noqa: E402
from bs4 import BeautifulSoup           # noqa: E402
try:
    from curl_cffi import requests as cffi_requests
    _HAS_CFFI = True
except ImportError:
    _HAS_CFFI = False


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
    "Accept-Encoding":           "gzip, deflate, br",
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


# ── Sessions ──────────────────────────────────────────────────────────────────

def make_session(referer=None):
    s = requests.Session()
    s.verify = False
    s.headers.update({"User-Agent": UA_SAFARI, "Accept-Language": "en-US,en;q=0.9"})
    if referer:
        s.headers["Referer"] = referer
    return s


# ── HTML fetching — 4 strategies, fully automatic ────────────────────────────

def fetch_html(url, session, verbose=False):
    """
    Try 4 strategies in order until one returns HTML:
      1. curl-cffi  — impersonates Chrome TLS fingerprint (beats Akamai/Cloudflare)
      2. requests   — fast, blocked by TLS fingerprinting on some CDNs
      3. urllib     — different SSL stack, bypasses some blocks
      4. curl       — native binary with full browser headers
    """
    import ssl

    def _is_html(text):
        return text and "<html" in text[:2000].lower()

    # 1. curl-cffi — exact Chrome TLS fingerprint
    if _HAS_CFFI:
        try:
            r = cffi_requests.get(
                url, impersonate="chrome124",
                headers={"Accept-Language": "en-US,en;q=0.9"},
                timeout=20, verify=False,
            )
            if r.status_code == 200 and _is_html(r.text):
                if verbose:
                    print("    [html] curl-cffi ok")
                return r.text
        except Exception:
            pass

    # 2. requests
    try:
        hdrs = {**BROWSER_HEADERS, "User-Agent": UA_CHROME}
        r = session.get(url, headers=hdrs, timeout=15)
        if r.status_code == 200 and _is_html(r.text):
            if verbose:
                print("    [html] requests ok")
            return r.text
    except Exception:
        pass

    # 3. urllib
    try:
        req = urllib.request.Request(url, headers={**BROWSER_HEADERS, "User-Agent": UA_CHROME})
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode    = ssl.CERT_NONE
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            if resp.status == 200:
                html = resp.read().decode("utf-8", errors="replace")
                if _is_html(html):
                    if verbose:
                        print("    [html] urllib ok")
                    return html
    except Exception:
        pass

    # 4. system curl
    try:
        result = subprocess.run(
            [
                "curl", "-sL", "--max-time", "20", "-k",
                "-A", UA_CHROME,
                "-H", f"Accept: {BROWSER_HEADERS['Accept']}",
                "-H", "Accept-Language: en-US,en;q=0.9",
                "-H", "Cache-Control: max-age=0",
                "-H", "Sec-Fetch-Dest: document",
                "-H", "Sec-Fetch-Mode: navigate",
                "-H", "Sec-Fetch-Site: none",
                "-H", "Sec-Fetch-User: ?1",
                "-H", "Upgrade-Insecure-Requests: 1",
                url,
            ],
            capture_output=True, timeout=25,
        )
        html = result.stdout.decode("utf-8", errors="replace")
        if result.returncode == 0 and _is_html(html):
            if verbose:
                print("    [html] curl ok")
            return html
    except Exception:
        pass

    return None


# ── URL extraction ────────────────────────────────────────────────────────────

def get_ext(url):
    path = urlparse(url).path.split("?")[0]
    ext  = Path(path).suffix.lstrip(".").lower()
    return ext if ext else None


def extract_urls(html, base_url, allowed_types):
    soup  = BeautifulSoup(html, "html.parser")
    found = set()

    tag_attrs = [
        ("a",      ["href"]),
        ("img",    ["src", "data-src", "data-original", "data-lazy-src", "data-srcset"]),
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
                val = el.get(attr, "")
                if not val:
                    continue
                for raw in [v.strip().split()[0] for v in val.split(",")]:
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

    # Raw regex scan — catches URLs in JS/JSON blocks
    for m in re.finditer(
        r'["\']((https?:)?//[^"\'<>\s]+\.(' + "|".join(ALL_EXTENSIONS) + r'))["\']',
        html, re.I,
    ):
        raw = m.group(1)
        if raw.startswith("//"):
            raw = "https:" + raw
        ext = get_ext(raw)
        if ext and ext in allowed_types:
            found.add(raw)

    return found


def crawl(session, url, depth, allowed_types, visited=None, verbose=False):
    if visited is None:
        visited = set()
    if url in visited or depth < 0:
        return set()
    visited.add(url)

    html = fetch_html(url, session, verbose)
    if not html:
        return set()

    found = extract_urls(html, url, allowed_types)
    base  = "{uri.scheme}://{uri.netloc}".format(uri=urlparse(url))

    if depth > 0:
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            full = urljoin(url, a["href"])
            ext  = get_ext(full)
            if ext is None and full.startswith(base) and full not in visited:
                found |= crawl(session, full, depth - 1, allowed_types, visited, verbose)

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


def auto_output_dir(url):
    p   = urlparse(url)
    host = p.netloc.replace("www.", "")
    path = p.path.strip("/").replace("/", "-")
    name = f"{host}-{path}" if path else host
    return Path(re.sub(r"[^\w.\-]", "_", name))


def download_file(session, url, output_dir, delay=0.0, retries=3):
    ext      = get_ext(url)
    category = FOLDER_MAP.get(ext, "others")
    folder   = output_dir / category
    folder.mkdir(parents=True, exist_ok=True)

    filename = safe_filename(url)
    dest     = folder / filename

    counter = 1
    orig_dest = dest
    while dest.exists() and dest.stat().st_size > 0:
        stem   = Path(filename).stem
        suffix = Path(filename).suffix
        dest   = folder / f"{stem}_{counter}{suffix}"
        counter += 1

    if dest != orig_dest and (dest.parent / filename).stat().st_size > 0:
        return "skip", orig_dest, orig_dest.stat().st_size

    if delay:
        time.sleep(delay)

    for attempt in range(retries):
        try:
            r = session.get(url, timeout=30, stream=True)
            if r.status_code != 200:
                if attempt == retries - 1:
                    return "error", url, f"HTTP {r.status_code}"
                time.sleep(1)
                continue

            ct = r.headers.get("Content-Type", "")
            if "text/html" in ct:
                return "error", url, "HTML page (not a file)"

            data = b""
            for chunk in r.iter_content(chunk_size=65536):
                data += chunk

            dest.write_bytes(data)
            return "ok", dest, len(data)

        except Exception as ex:
            if attempt == retries - 1:
                return "error", url, str(ex)
            time.sleep(1)

    return "error", url, "max retries reached"


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
    parser.add_argument("url",             help="Target URL")
    parser.add_argument("-o", "--output",  default=None,        help="Output directory (default: auto from URL)")
    parser.add_argument("-t", "--types",   default="all",       help="Types: images,videos,documents,audio,all (default: all)")
    parser.add_argument("-d", "--depth",   type=int, default=1, help="Crawl depth (default: 1)")
    parser.add_argument("-j", "--threads", type=int, default=8, help="Concurrent downloads (default: 8)")
    parser.add_argument(      "--delay",   type=float, default=0.1, help="Delay between requests (default: 0.1s)")
    parser.add_argument(      "--no-crawl", action="store_true", help="Download the URL directly without crawling")
    parser.add_argument(      "--list",    action="store_true", help="List found URLs without downloading")
    parser.add_argument(      "--html",    default=None,        help="Use a local HTML file (bypass bot protection)")
    parser.add_argument(      "--verbose", action="store_true", help="Show which fetch strategy succeeded")
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
    args = parse_args()
    banner()

    output_dir  = Path(args.output).expanduser().resolve() if args.output else auto_output_dir(args.url).resolve()
    allowed_ext = resolve_types(args.types)
    session     = make_session(referer=args.url)

    engine = "curl-cffi+requests+urllib+curl" if _HAS_CFFI else "requests+urllib+curl"

    print(f"  Target  : {args.url}")
    print(f"  Output  : {output_dir}")
    print(f"  Types   : {args.types}  ({len(allowed_ext)} extensions)")
    print(f"  Depth   : {args.depth}")
    print(f"  Threads : {args.threads}")
    print(f"  Engine  : {engine}")
    print()

    # ── Discover ──────────────────────────────────────────────────────────────
    if args.no_crawl:
        ext  = get_ext(args.url)
        urls = {args.url} if ext and ext in allowed_ext else set()
        if not urls:
            print(f"[!] Extension '.{ext}' not in selected types.")
            sys.exit(1)

    elif args.html:
        print(f"[*] Using local HTML: {args.html}")
        html = Path(args.html).read_text(encoding="utf-8", errors="replace")
        urls = extract_urls(html, args.url, allowed_ext)

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
