# scrape_wiki.py

import os
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse

# 1. Configuration
START_URL = "https://sites.google.com/a/cabinetsensesoftware.com/cabinetsense-wiki/home"
OUTPUT_DIR = "cabinetsense-knowledgebase"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# 2. Helpers
seen = set()
base_netloc = urlparse(START_URL).netloc

def is_internal(link: str) -> bool:
    parsed = urlparse(link)
    return (not parsed.netloc) or parsed.netloc.endswith(base_netloc)

def save_text(url: str, soup: BeautifulSoup):
    # Adjust this selector if your page layout changes
    container = soup.select_one("div.freebirdFormviewerViewGridRow") or soup.body
    text = container.get_text("\n\n", strip=True)
    # Build a safe filename from URL path
    path = urlparse(url).path.strip("/").replace("/", "_") or "home"
    fname = os.path.join(OUTPUT_DIR, f"{path}.txt")
    with open(fname, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"Saved: {fname}")

def crawl_page(url: str):
    if url in seen:
        return
    print(f"Crawling: {url}")
    seen.add(url)
    resp = requests.get(url)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    # 3. Save the main content
    save_text(url, soup)

    # 4. Find all internal links in the sidebar / nav
    for a in soup.select("nav a[href], div.docs-nav a[href]"):
        href = a["href"].split("#")[0]
        full = urljoin(url, href)
        if is_internal(full) and full not in seen:
            crawl_page(full)

if __name__ == "__main__":
    crawl_page(START_URL)
    print(f"\nDone! Scraped {len(seen)} pages into {OUTPUT_DIR}/")
