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
session.headers["User-Agent"] = "fcc-exhibit-downloader/1.2"

os.makedirs(OUTDIR, exist_ok=True)

def slugify(s, maxlen=140):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^\w\s.-]", "", s).strip()
    s = re.sub(r"\s+", "_", s)
    return (s or "exhibit")[:maxlen]

def fetch_html(url):
    r = session.get(url, timeout=30)
    r.raise_for_status()
    return r.text

def nearby_text_contains_pdf_marker(a_tag, window_nodes=12):
    """Look through the next few nodes after the anchor for 'Adobe Acrobat PDF'."""
    texts = []
    for node in itertools.islice(a_tag.next_elements, 0, window_nodes):
        if isinstance(node, Tag) and node.name == "a":
            break  # next link block → stop lookahead
        if isinstance(node, Tag):
            texts.append(node.get_text(" ", strip=True))
        elif isinstance(node, NavigableString):
            texts.append(str(node))
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

DOCID_RE = re.compile(r"-(\d+)\.pdf$", re.I)

def extract_doc_id(pdf_url: str) -> str:
    """Get trailing numeric id like ...-8024702.pdf -> '8024702' (optional)."""
    m = DOCID_RE.search(pdf_url)
    if m:
        return m.group(1)
    # fallback: any digits at end of path without extension
    path = urlparse(pdf_url).path
    base = os.path.basename(path)
    base_noext, _ = os.path.splitext(base)
    tail_digits = re.search(r"(\d+)$", base_noext)
    return tail_digits.group(1) if tail_digits else ""

def remote_size(url: str) -> int | None:
    try:
        h = session.head(url, allow_redirects=True, timeout=30)
        if 200 <= h.status_code < 400:
            cl = h.headers.get("Content-Length")
            if cl and cl.isdigit():
                return int(cl)
    except requests.RequestException:
        pass
    return None

def download(pdf_url, base_title):
    # build a filename that includes the doc id to reduce collisions
    docid = extract_doc_id(pdf_url)
    title_slug = slugify(base_title)
    fname = f"{title_slug}{('_' + docid) if docid else ''}.pdf"
    out = os.path.join(OUTDIR, fname)

    # if a file already exists with same name & same size as remote, skip
    rsize = remote_size(pdf_url)
    if os.path.exists(out) and rsize is not None:
        lsize = os.path.getsize(out)
        if lsize == rsize:
            print(f"skip (already have identical file): {out}")
            return

    print(f"↓ {pdf_url}\n→ {out}")
    with session.get(pdf_url, stream=True, timeout=180) as r:
        r.raise_for_status()
        tmp = out + ".part"
        with open(tmp, "wb") as fh:
            for chunk in r.iter_content(chunk_size=1024 * 64):
                if chunk:
                    fh.write(chunk)
        os.replace(tmp, out)
    print("✓ saved\n")

def main():
    print(f"Scanning: {START_URL}")
    main_html = fetch_html(START_URL)
    soup = BeautifulSoup(main_html, "html.parser")

    # 1) Collect exhibit page links whose nearby text says "Adobe Acrobat PDF"
    exhibit_pages = []
    for a in soup.find_all("a", href=True):
        href = urljoin(START_URL, a["href"])
        if "/BCG-E8726A/" in href and href.rstrip("/") != START_URL.rstrip("/"):
            if nearby_text_contains_pdf_marker(a):
                title = (a.get_text() or "").strip() or "exhibit"
                exhibit_pages.append((href, title))

    # de-dup exhibit pages (by URL), preserve order
    seen_ex_pages = set()
    ex_pages = []
    for u, t in exhibit_pages:
        if u not in seen_ex_pages:
            ex_pages.append((u, t))
            seen_ex_pages.add(u)

    print(f"Found {len(ex_pages)} exhibit pages marked as PDF.\n")

    # 2) Visit each exhibit page → find the actual PDF URL
    seen_pdf_urls = set()  # de-dup by final file URL
    for ex_url, title in ex_pages:
        try:
            time.sleep(SLEEP)
            ex_html = fetch_html(ex_url)
            pdf_url = pick_pdf_from_exhibit(ex_url, ex_html)
            if not pdf_url:
                print(f"! No PDF link found on: {ex_url}")
                continue

            # de-dup: skip if we already queued/downloaded this exact PDF URL
            if pdf_url in seen_pdf_urls:
                print(f"skip (same PDF URL already handled): {pdf_url}")
                continue
            seen_pdf_urls.add(pdf_url)

            download(pdf_url, title)

        except requests.HTTPError as e:
            print(f"! HTTP error for {ex_url}: {e}")
        except Exception as e:
            print(f"! Error on {ex_url}: {e}")

if __name__ == "__main__":
    main()
