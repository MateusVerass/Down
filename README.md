# Down

Web media downloader. Crawls a URL and downloads **images**, **videos**, and **documents** — organized by type.

## Install

```bash
pip install requests beautifulsoup4
```

## Usage

```
down.py <URL> [options]
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
  --list                List found URLs without downloading
```

## Examples

```bash
# Download everything from a page
python down.py https://example.com

# Download only images and videos
python down.py https://example.com -t images,videos

# Set output folder
python down.py https://example.com -o ~/Downloads/site

# Crawl 2 levels deep with 8 threads
python down.py https://example.com -d 2 -j 8

# Just list what would be downloaded
python down.py https://example.com --list

# Download a single file directly
python down.py https://example.com/file.pdf --no-crawl
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
| images    | jpg, jpeg, png, gif, webp, svg, bmp, tiff, ico, avif |
| videos    | mp4, mkv, avi, mov, webm, flv, wmv, m4v, ts, mpeg |
| documents | pdf, doc, docx, xls, xlsx, ppt, pptx, txt, csv, zip, rar, 7z |
| audio     | mp3, wav, ogg, flac, aac, m4a, opus, wma |
