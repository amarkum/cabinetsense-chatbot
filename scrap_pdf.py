#!/usr/bin/env python3
"""
scrape_pdf_sequential.py — sequential CabinetSense wiki archiver.

• Crawls each internal page in depth‑first order, one at a time
• Cleans HTML, embeds images as data URIs, renders directly to PDF
• Downloads all "Release Notes" PDFs from BUILD_HISTORY

Dependencies: requests, beautifulsoup4, xhtml2pdf, certifi, python‑dotenv
"""

from __future__ import annotations
import os
import re
import sys
import time
import hashlib
import ssl
import base64
import contextlib
import io
from io import BytesIO
from urllib.parse import urljoin, urlparse, parse_qs
from pathlib import Path

import requests
from bs4 import BeautifulSoup, Comment
from xhtml2pdf import pisa
import xhtml2pdf.util as _xu
import certifi
from dotenv import load_dotenv

# ─── MONKEY PATCH ────────────────────────────────────────────────────────────
# Suppress xhtml2pdf.getSize warnings by replacing the implementation
_old_getSize = _xu.getSize

def _safe_getSize(value, *args, **kwargs):
    if isinstance(value, str) and value.strip().endswith('%'):
        try:
            return float(value.strip().rstrip('%'))
        except ValueError:
            return 0.0
    try:
        return _old_getSize(value, *args, **kwargs)
    except Exception:
        return 0.0

_xu.getSize = _safe_getSize

# ─── CONFIGURATION ───────────────────────────────────────────────────────────
load_dotenv()
ROOT_URL      = os.getenv("ROOT_URL", "https://sites.google.com/a/cabinetsensesoftware.com/cabinetsense-wiki/home")
BUILD_HISTORY = os.getenv("BUILD_HISTORY", "https://sites.google.com/a/cabinetsensesoftware.com/cabinetsense-wiki/build-history")
OUT_DIR       = Path(os.getenv("KNOWLEDGE_DIR", "cabinetsense-knowledgebase"))
PAGES_DIR     = OUT_DIR / "pages"
RELEASES_DIR  = OUT_DIR / "releases"
for folder in (PAGES_DIR, RELEASES_DIR):
    folder.mkdir(parents=True, exist_ok=True)

# ─── SESSION SETUP ───────────────────────────────────────────────────────────
session = requests.Session()
session.verify = certifi.where()
session.headers.update({"User-Agent": "cabinetsense-wiki-scraper/1.0"})
visited: set[str] = set()

# ─── HELPERS ──────────────────────────────────────────────────────────────────
def sanitize(url: str, ext: str) -> str:
    parsed = urlparse(url)
    name = re.sub(r"[^A-Za-z0-9]+", "_", parsed.path.strip("/")) or "page"
    digest = hashlib.sha1(url.encode()).hexdigest()[:8]
    return f"{name}_{digest}.{ext}"

BAD_HOSTS = ("fonts.googleapis.com", "gstatic.com")

def clean_html(raw_html: str) -> str:
    soup = BeautifulSoup(raw_html, "html.parser")
    # Remove scripts and comments
    for tag in soup(["script", "noscript"]):
        tag.decompose()
    for comment in soup(text=lambda t: isinstance(t, Comment)):
        comment.extract()
    # Embed images as data URIs
    for img in soup.find_all("img", src=True):
        src = img["src"]
        if not src.startswith("data:"):
            try:
                resp = session.get(src, timeout=30)
                resp.raise_for_status()
                ctype = resp.headers.get("Content-Type", "image/png")
                data_b64 = base64.b64encode(resp.content).decode('ascii')
                img["src"] = f"data:{ctype};base64,{data_b64}"
            except Exception:
                img.decompose()
    # Remove external font CSS
    for link in soup.find_all("link", rel="stylesheet"):
        href = link.get("href", "")
        if any(host in href for host in BAD_HOSTS):
            link.decompose()
    # Normalize input types
    for inp in soup.find_all("input"):
        if inp.get("type", "text") not in ("text", "hidden", "checkbox"):
            inp["type"] = "text"
    return str(soup)

# ─── SAVE PDF ─────────────────────────────────────────────────────────────────
def save_html_as_pdf(url: str, html: str) -> None:
    pdf_file = PAGES_DIR / sanitize(url, "pdf")
    if pdf_file.exists():
        return
    safe_html = clean_html(html)
    buffer = BytesIO()
    # Suppress internal warnings during PDF generation
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        result = pisa.CreatePDF(safe_html, dest=buffer, link_callback=lambda uri, base: uri)
    if result.err:
        print(f"Error: PDF render failed for {url}", file=sys.stderr)
    else:
        buffer.seek(0)
        pdf_file.write_bytes(buffer.read())
        print(f"Saved PDF: {pdf_file.relative_to(OUT_DIR)}  Source: {url}")

# ─── CRAWL ─────────────────────────────────────────────────────────────────────
def is_internal(link: str) -> bool:
    p = urlparse(link)
    return (not p.netloc) or p.netloc == urlparse(ROOT_URL).netloc

def crawl(url: str) -> None:
    if url in visited:
        return
    visited.add(url)
    try:
        response = session.get(url, timeout=30)
        response.raise_for_status()
    except Exception as e:
        print(f"Error: Failed to fetch {url}: {e}", file=sys.stderr)
        return
    save_html_as_pdf(url, response.text)
    soup = BeautifulSoup(response.text, "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"].split('#')[0]
        if href.lower().startswith("mailto:"):
            continue
        full = urljoin(url, href)
        if is_internal(full):
            crawl(full)

# ─── DOWNLOAD RELEASE NOTES ───────────────────────────────────────────────────
def get_drive_direct_link(href: str) -> str:
    m = re.search(r'/d/([a-zA-Z0-9_-]+)', href)
    if m:
        file_id = m.group(1)
    else:
        qp = parse_qs(urlparse(href).query)
        file_id = qp.get('id', [None])[0]
    if file_id:
        return f"https://drive.google.com/uc?export=download&id={file_id}"
    return href

def looks_like_pdf(chunk: bytes) -> bool:
    # PDF files start with “%PDF”
    return chunk.startswith(b"%PDF")

def download_release_pdfs() -> None:
    try:
        resp = session.get(BUILD_HISTORY, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        print(f"Error fetching release notes page: {e}", file=sys.stderr)
        return

    soup = BeautifulSoup(resp.text, "html.parser")
    RELEASES_DIR.mkdir(parents=True, exist_ok=True)
    count = 0

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.lower().endswith(".pdf"):
            pdf_url = urljoin(BUILD_HISTORY, href)
        elif "drive.google.com" in href:
            pdf_url = get_drive_direct_link(href)
        else:
            continue

        dest = RELEASES_DIR / sanitize(pdf_url, "pdf")
        if dest.exists():
            continue

        try:
            r = session.get(pdf_url, stream=True, timeout=60)
            # if Drive denies access, status might be 403
            if r.status_code == 403:
                print(f"Skipping forbidden PDF: {pdf_url}", file=sys.stderr)
                continue

            # Peek at first chunk to verify it’s a PDF
            first_chunk = next(r.iter_content(1024), b"")
            if not looks_like_pdf(first_chunk):
                print(f"Skipping non‑PDF content at: {pdf_url}", file=sys.stderr)
                continue

            # Write out the first chunk + the rest
            with open(dest, "wb") as f:
                f.write(first_chunk)
                for chunk in r.iter_content(65536):
                    f.write(chunk)

            print(f"Downloaded: {dest.relative_to(OUT_DIR)}  Source: {pdf_url}")
            count += 1

        except Exception as e:
            print(f"Error downloading {pdf_url}: {e}", file=sys.stderr)

    print(f"Total release PDFs downloaded: {count}")
# ─── MAIN ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    start = time.time()
    print(f"Starting crawl: {ROOT_URL}")
    # crawl(ROOT_URL)
    print(f"Completed crawling {len(visited)} pages.")
    print(f"Downloading release notes from {BUILD_HISTORY}")
    download_release_pdfs()
    duration = time.time() - start
    print(f"Finished in {duration:.1f} seconds. Output directory: {OUT_DIR}")