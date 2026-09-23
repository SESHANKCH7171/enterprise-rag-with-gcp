import re
import time
from pathlib import Path
from collections import deque
from urllib.parse import urljoin, urlparse, urldefrag
# pyrefly: ignore [missing-import]
import requests


BASE = "https://docs.stripe.com"
LLMS_URL = f"{BASE}/llms.txt"

OUTPUT_DIR = Path("DATA/true_data/stripe")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

session = requests.Session()

session.headers.update({
    "User-Agent": "EnterpriseRAGResearchCrawler/1.0",
    "Accept": "text/markdown,text/plain,*/*",
})


def normalize_url(url: str) -> str | None:
    """Keep only Stripe docs URLs and convert them to Markdown URLs."""

    url, _ = urldefrag(url)

    parsed = urlparse(url)

    if parsed.netloc != "docs.stripe.com":
        return None

    if parsed.scheme not in {"http", "https"}:
        return None

    path = parsed.path

    if not path:
        path = "/"

    # Convert normal docs pages into their markdown equivalent
    if not path.endswith(".md"):
        path = path.rstrip("/") + ".md"

    return f"{BASE}{path}"


def safe_filename(url: str) -> Path:
    """Turn URL into a deterministic local filename."""

    parsed = urlparse(url)

    path = parsed.path.strip("/")

    if not path:
        path = "index"

    filename = path.replace("/", "__")

    if filename.endswith(".md"):
        filename = filename[:-3]

    return OUTPUT_DIR / f"{filename}.md"


def extract_links(markdown: str, current_url: str):
    """Extract Markdown links from a document."""

    pattern = r"\[[^\]]+\]\(([^)]+)\)"

    results = set()

    for href in re.findall(pattern, markdown):

        absolute = urljoin(current_url, href)

        normalized = normalize_url(absolute)

        if normalized:
            results.add(normalized)

    return results


def download(url: str) -> str:
    response = session.get(url, timeout=30)
    response.raise_for_status()
    return response.text


# -------------------------------------------------
# 1. Get Stripe's documentation index
# -------------------------------------------------

print("Downloading llms.txt...")

index = download(LLMS_URL)

seed_urls = extract_links(index, LLMS_URL)

print(f"Initial URLs discovered: {len(seed_urls)}")


# -------------------------------------------------
# 2. Crawl recursively
# -------------------------------------------------

queue = deque(seed_urls)
visited = set()

while queue:

    url = queue.popleft()

    if url in visited:
        continue

    visited.add(url)

    try:

        print(f"[{len(visited)}] {url}")

        content = download(url)

        output_file = safe_filename(url)

        output_file.parent.mkdir(
            parents=True,
            exist_ok=True
        )

        output_file.write_text(
            content,
            encoding="utf-8"
        )

        # Discover additional docs pages
        new_links = extract_links(
            content,
            url
        )

        for link in new_links:

            if link not in visited:
                queue.append(link)

        # Be polite
        time.sleep(0.25)

    except Exception as e:

        print(
            f"FAILED: {url}\n"
            f"Reason: {e}"
        )

print()
print("================================")
print("CRAWL COMPLETE")
print("================================")
print(f"Documents downloaded : {len(visited)}")
print(f"Output directory     : {OUTPUT_DIR}")