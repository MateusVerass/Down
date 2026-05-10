# Down

Web media downloader. Crawls a URL and downloads **images**, **videos**, and **documents** — organized by type.

## Install

```bash
pip install requests beautifulsoup4
```

## Usage

```
python3 down.py <URL> [options]
```

```
positional arguments:
  url                   Target URL to crawl

options:
  -o, --output DIR      Output directory (default: ./down_output)
  -t, --types LIST      Types: images, videos, documents, audio, all (default: all)
  -d, --depth INT       Crawl depth (default: 1)
  -j, --threads INT     Concurrent downloads (default: 4)
  --delay FLOAT         Delay between requests in seconds (default: 0.2)
  --ua STRING           Custom User-Agent
  --no-crawl            Download the URL directly without crawling
  --html FILE           Use a local HTML file instead of fetching (bypass bot protection)
  --list                List found URLs without downloading
```

## Examples

```bash
# Download everything from a page
python3 down.py https://example.com

# Download only images and videos
python3 down.py https://example.com -t images,videos

# Set output folder
python3 down.py https://example.com -o ~/Downloads/site

# Crawl 2 levels deep with 8 threads
python3 down.py https://example.com -d 2 -j 8

# Just list what would be downloaded
python3 down.py https://example.com --list

# Download a single file directly
python3 down.py https://example.com/file.pdf --no-crawl
```

## Real example — war.gov/UFO declassified UAP files

The U.S. Department of War's [UFO page](https://www.war.gov/UFO/) hosts declassified UAP reports, FBI photos, NASA images and DoD documents.

The site uses Akamai CDN bot protection that blocks automated HTML fetching.
Down handles this with the `--html` flag: save the page manually in your browser and pass it as input.

**Step 1** — Save the page in your browser:
```
Right-click → Save As → war.gov-UFO.html
```

Or with curl (which uses native TLS, bypassing the fingerprint check):
```bash
curl -sL -A "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36" \
  -H "Sec-Fetch-Dest: document" -H "Sec-Fetch-Mode: navigate" \
  https://www.war.gov/UFO/ -o war.gov-UFO.html
```

**Step 2** — Run Down:
```bash
python3 down.py https://www.war.gov/UFO/ \
  --html war.gov-UFO.html \
  -t images,documents \
  -o ufo_files
```

**Output:**
```
[*] Using local HTML: war.gov-UFO.html
[+] Found 23 file(s)

  [ 1/23]  OK      7.7 MB  DOD-STRATEGIC-MGMT-PLAN-2023.PDF
  [ 2/23]  OK      6.7 MB  2026-NATIONAL-DEFENSE-STRATEGY.PDF
  [ 3/23]  OK      1.5 MB  2024-04-30-Composite-Sketch.jpg
  [ 4/23]  OK      1.2 MB  FBI-Photo-1.jpg
  [ 5/23]  OK      1.5 MB  FBI-Photo-B2.jpg
  ...

============================================================
  Done
  OK   : 22
  Fail : 0
  Size : 34.3 MB
  Dir  : ./ufo_files
============================================================

  documents/
    2026-NATIONAL-DEFENSE-STRATEGY.PDF         6.7 MB
    DOD-STRATEGIC-MGMT-PLAN-2023.PDF           7.7 MB
  images/
    2024-04-30-Composite-Sketch.jpg            1.5 MB
    DOW-UAP-PR19-...Middle-East-May-2022.jpg   1.2 MB
    DOW-UAP-PR26-...UAE-October-2023.jpg       1.0 MB
    FBI-Photo-1.jpg                            1.2 MB
    FBI-Photo-A5.jpg                           1.1 MB
    NASA-UAP-VM6-Apollo-17-1972.jpg            1.5 MB
    ...
```

## Output structure

```
down_output/
  images/       jpg, jpeg, png, gif, webp, svg, bmp, tiff, ico, avif
  videos/       mp4, mkv, avi, mov, webm, flv, wmv, m4v, ts
  documents/    pdf, doc, docx, xls, xlsx, ppt, zip, csv, ...
  audio/        mp3, wav, ogg, flac, aac, m4a, opus
  others/       anything else
```

## Supported types

| Type      | Extensions |
|-----------|-----------|
| images    | jpg, jpeg, jfif, png, gif, webp, apng, bmp, tiff, ico, avif, heic, heif, psd, xcf, svg, svgz, ai, eps, raw, cr2, cr3, nef, arw, dng, orf, raf, rw2 … |
| videos    | mp4, m4v, mkv, avi, mov, wmv, flv, webm, mpeg, mpg, ts, m2ts, 3gp, 3g2, f4v, vob, ogv, rm, rmvb, mxf, dv, divx, xvid, qt … |
| documents | pdf, doc, docx, odt, rtf, xls, xlsx, csv, ppt, pptx, txt, md, json, xml, epub, mobi, djvu, zip, rar, 7z, tar, gz, bz2, iso, dmg … |
| audio     | mp3, m4a, aac, ogg, opus, flac, wav, aiff, wma, ape, mka, mid, amr, dsd … |

## Notes

- Sites protected by Akamai, Cloudflare or similar CDNs may block automated HTML fetching.
  Use `--html` with a browser-saved page to bypass this.
- Down automatically tries `requests`, `urllib`, and `curl` when fetching HTML.
- Downloads validate `Content-Type` to skip pages disguised as files.
- Concurrent downloads with `-j` to speed up large batches.
