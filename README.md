# Down

Paste a URL — Down crawls it and downloads **everything** automatically.  
Images, videos, documents, audio. Beats Akamai and Cloudflare bot protection.

## Install

```bash
pip install requests beautifulsoup4 curl-cffi
```

> `curl-cffi` impersonates Chrome's exact TLS fingerprint, bypassing CDN bot detection.  
> If it's not installed, Down auto-installs it on first run.

## Usage

```bash
python3 down.py <URL>
```

That's it. Output folder is named automatically from the URL.

```
positional arguments:
  url                   Target URL

options:
  -o, --output DIR      Output directory (default: auto from URL)
  -t, --types LIST      images, videos, documents, audio, all (default: all)
  -d, --depth INT       Crawl depth (default: 1)
  -j, --threads INT     Concurrent downloads (default: 8)
  --delay FLOAT         Delay between requests (default: 0.1s)
  --no-crawl            Download the URL directly without crawling
  --list                List found URLs without downloading
  --html FILE           Use a local HTML file (last resort bypass)
  --verbose             Show which fetch strategy succeeded
```

## Examples

```bash
# Download everything from a page
python3 down.py https://example.com

# Only images and videos
python3 down.py https://example.com -t images,videos

# Crawl 2 levels deep, 8 parallel threads
python3 down.py https://example.com -d 2 -j 8

# See what would be downloaded without downloading
python3 down.py https://example.com --list

# Download one file directly
python3 down.py https://example.com/video.mp4 --no-crawl
```

## Real example — war.gov/UFO declassified UAP files

```bash
python3 down.py https://www.war.gov/UFO/
```

```
  Target  : https://www.war.gov/UFO/
  Output  : ./war.gov-UFO
  Types   : all  (166 extensions)
  Depth   : 1
  Threads : 8
  Engine  : curl-cffi+requests+urllib+curl

[*] Crawling...
[+] Found 23 file(s)

  [ 1/23]  OK      7.7 MB  DOD-STRATEGIC-MGMT-PLAN-2023.PDF
  [ 2/23]  OK      6.7 MB  2026-NATIONAL-DEFENSE-STRATEGY.PDF
  [ 3/23]  OK      1.5 MB  2024-04-30-Composite-Sketch.jpg
  [ 4/23]  OK      1.2 MB  FBI-Photo-1.jpg
  ...

============================================================
  Done
  OK   : 22
  Size : 34.3 MB
  Dir  : ./war.gov-UFO
============================================================

  documents/
    2026-NATIONAL-DEFENSE-STRATEGY.PDF         6.7 MB
    DOD-STRATEGIC-MGMT-PLAN-2023.PDF           7.7 MB
  images/
    2024-04-30-Composite-Sketch.jpg            1.5 MB
    FBI-Photo-1.jpg                            1.2 MB
    NASA-UAP-VM6-Apollo-17-1972.jpg            1.5 MB
    DOW-UAP-PR19-...Middle-East-2022.jpg       1.2 MB
    ...
```

## How it bypasses bot protection

Down tries 4 strategies automatically, in order:

| # | Strategy | Bypasses |
|---|----------|---------|
| 1 | **curl-cffi** — Chrome TLS fingerprint impersonation | Akamai, Cloudflare, Imperva |
| 2 | **requests** — fast, standard Python HTTP | Basic blocks |
| 3 | **urllib** — different SSL stack | Some TLS-based blocks |
| 4 | **system curl** — native binary with full Sec-Fetch headers | Most remaining CDN checks |

If all 4 fail (e.g. login required), save the page with your browser and use `--html page.html`.

## Output structure

```
<site-name>/
  images/       jpg, png, gif, webp, heic, avif, svg, psd, raw, cr2, dng …
  videos/       mp4, mkv, avi, mov, webm, ts, 3gp, flv, rmvb, mxf …
  documents/    pdf, docx, xlsx, epub, zip, iso, dmg …
  audio/        mp3, flac, wav, aac, ogg, opus, aiff, dsd …
  others/       anything else
```

## Supported extensions — 166 total

| Type | Count | Formats |
|------|-------|---------|
| images | 48 | jpg jpeg png gif webp heic avif psd xcf svg ai eps raw cr2 cr3 nef arw dng … |
| videos | 45 | mp4 mkv avi mov webm ts m2ts 3gp f4v vob rmvb mxf dv divx xvid … |
| documents | 47 | pdf docx xlsx pptx epub mobi djvu zip rar 7z tar gz iso dmg deb … |
| audio | 27 | mp3 flac wav aac ogg opus aiff wma ape dsd mid amr … |
