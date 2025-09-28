#!/usr/bin/env python3
# pip install requests beautifulsoup4

import os, time, re, unicodedata, itertools
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup, NavigableString, Tag

START_URL = "https://fccid.io/BCG-E8726A"
OUTDIR = "fcc_exhibits"
SLEEP = 0.4

session = requests.Session()
session.headers["User-Agent"] = "fcc-exhibit-downloader/1.1"

os.makedirs(OUTDIR, exist_ok=True)

def slugify(s, maxlen=120):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^\w\s.-]", "", s).strip()
    s = re.sub(r"\s+", "_", s)
    return (s or "exhibit")[:maxlen]

def fetch_html(url):
    r = session.get(url, timeout=30)
    r.raise_for_status()
    return r.text

def nearby_text_contains_pdf_marker(a_tag, window_nodes=12):
    """Look through the next few nodes after the anchor for the marker."""
    texts = []
    for node in itertools.islice(a_tag.next_elements, 0, window_nodes):
        if isinstance(node, Tag) and node.name == "a":
            # stop if we hit the next link block
            break
        if isinstance(node, (NavigableString, Tag)):
            texts.append(node.get_text(" ", strip=True) if isinstance(node, Tag) else str(node))
    blob = " ".join(texts).lower()
    return "adobe acrobat pdf" in blob

def pick_pdf_from_exhibit(ex_url, html):
    soup = BeautifulSoup(html, "html.parser")

    # 1) direct .pdf link
    a = soup.find("a", href=lambda h: h and h.lower().endswith(".pdf"))
    if a:
        return urljoin(ex_url, a["href"])

    # 2) any link that looks like a download route
    a = soup.find("a", href=lambda h: h and "download" in h.lower())
    if a:
        return urljoin(ex_url, a["href"])

    # 3) iframe/embed with a pdf
    for tag in soup.find_all(["iframe", "embed"]):
        src = tag.get("src")
        if src and (src.lower().endswith(".pdf") or "download" in src.lower()):
            return urljoin(ex_url, src)

    return None

def download(url, base_name):
    fname = slugify(base_name)
    # try to keep .pdf; infer from URL if missing
    ext = os.path.splitext(urlparse(url).path)[1] or ".pdf"
    out = os.path.join(OUTDIR, fname + ext)

    # avoid overwriting
    stem, ext_only = os.path.splitext(out)
    i = 2
    while os.path.exists(out):
        out = f"{stem}({i}){ext_only}"
        i += 1

    print(f"↓ {url}\n→ {out}")
    with session.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        tmp = out + ".part"
        with open(tmp, "wb") as fh:
            for chunk in r.iter_content(chunk_size=1024*64):
                if chunk:
                    fh.write(chunk)
        os.replace(tmp, out)
    print("✓ saved\n")


def main():
    print(f"Scanning: {START_URL}")
    main_html = fetch_html(START_URL)
    soup = BeautifulSoup(main_html, "html.parser")

    # Collect exhibit links whose nearby text says "Adobe Acrobat PDF"
    exhibits = []
    for a in soup.find_all("a", href=True):
        href = urljoin(START_URL, a["href"])
        # keep only links that stay under this FCC ID path and are not the root
        if "/BCG-E8726A/" in href and href.rstrip("/") != START_URL.rstrip("/"):
            if nearby_text_contains_pdf_marker(a):
                title = (a.get_text() or "").strip() or "exhibit"
                exhibits.append((href, title))

    # de-dup, preserve order
    seen = set()
    uniq = [(u, t) for (u, t) in exhibits if not (u in seen or seen.add(u))]
    print(f"Found {len(uniq)} exhibit pages marked as PDF.\n")

    for ex_url, title in uniq:
        try:
            time.sleep(SLEEP)
            ex_html = fetch_html(ex_url)
            pdf_url = pick_pdf_from_exhibit(ex_url, ex_html)
            if not pdf_url:
                print(f"! No PDF link found on: {ex_url}")
                continue
            download(pdf_url, title)
        except Exception as e:
            print(f"! Error on {ex_url}: {e}")

if __name__ == "__main__":
    main()
