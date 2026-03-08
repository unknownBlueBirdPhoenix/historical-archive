import re
import shutil
import base64
from pathlib import Path
from urllib.parse import urlparse
from datetime import datetime

import requests
from bs4 import BeautifulSoup


# ========= 設定 =========
SOURCE_DIR = Path("source_html")
DOCS_DIR = Path("docs")
PAGES_DIR = DOCS_DIR / "pages"
ASSETS_DIR = DOCS_DIR / "assets"
COMMON_DIR = ASSETS_DIR / "common"
CSS_DIR = DOCS_DIR / "css"
META_DIR = DOCS_DIR / "meta"
ORIGINAL_DIR = DOCS_DIR / "original_html"
STYLE_FILE = CSS_DIR / "style.css"
INDEX_FILE = DOCS_DIR / "index.html"
NOJEKYLL_FILE = DOCS_DIR / ".nojekyll"

ARCHIVE_TITLE = "Historical Archive Mirror"
ARCHIVE_SUBTITLE = "Mirror of deleted web pages preserved for research and historical reference."
ORIGINAL_SOURCE_NAME = "Blue Bokeh Blog (currently offline)"
NOTES_TEXT = (
    "This archive preserves deleted pages recovered from web archives / saved copies.\n"
    "No claim of authorship. Formatting may differ from the original.\n"
    "Purpose: research and historical reference.\n"
)

REQUEST_TIMEOUT = 20
USER_AGENT = "Mozilla/5.0 (Archive Builder)"
# =======================


DEFAULT_CSS = """body {
  margin: 40px auto;
  max-width: 980px;
  padding: 0 16px;
  font-family: sans-serif;
  line-height: 1.7;
  color: #111;
  background: #fff;
}

img {
  max-width: 100%;
  height: auto;
}

.notice {
  border: 1px solid #ccc;
  background: #f8f8f8;
  padding: 12px;
  margin-bottom: 24px;
}

.meta {
  color: #666;
  font-size: 0.95rem;
}

a {
  color: #0645ad;
}

table {
  border-collapse: collapse;
}

td, th {
  vertical-align: top;
}

ul.page-list li {
  margin: 8px 0;
}
"""


def ensure_dirs() -> None:
    PAGES_DIR.mkdir(parents=True, exist_ok=True)
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    COMMON_DIR.mkdir(parents=True, exist_ok=True)
    CSS_DIR.mkdir(parents=True, exist_ok=True)
    META_DIR.mkdir(parents=True, exist_ok=True)
    ORIGINAL_DIR.mkdir(parents=True, exist_ok=True)

    if not STYLE_FILE.exists():
        STYLE_FILE.write_text(DEFAULT_CSS, encoding="utf-8")

    # Jekyll無効化
    if not NOJEKYLL_FILE.exists():
        NOJEKYLL_FILE.write_text("", encoding="utf-8")


def safe_ext_from_mime(mime: str) -> str:
    mime = mime.lower()
    mapping = {
        "image/jpeg": "jpg",
        "image/jpg": "jpg",
        "image/png": "png",
        "image/gif": "gif",
        "image/webp": "webp",
        "image/svg+xml": "svg",
        "image/x-icon": "ico",
        "image/vnd.microsoft.icon": "ico",
        "image/bmp": "bmp",
        "image/avif": "avif",
    }
    return mapping.get(mime, "bin")


def safe_ext_from_data_uri_header(header: str) -> str:
    m = re.search(
        r"data:([a-zA-Z0-9.+-]+/[a-zA-Z0-9.+-]+(?:\+[a-zA-Z0-9.+-]+)?);base64",
        header,
    )
    if not m:
        return "bin"
    return safe_ext_from_mime(m.group(1))


def save_data_uri_to_file(data_uri: str, out_dir: Path, filename_stem: str) -> str | None:
    if not data_uri.startswith("data:") or ";base64," not in data_uri:
        return None

    header, data = data_uri.split(",", 1)
    ext = safe_ext_from_data_uri_header(header)
    filename = f"{filename_stem}.{ext}"
    out_path = out_dir / filename

    try:
        out_path.write_bytes(base64.b64decode(data))
        return filename
    except Exception as e:
        print(f"[WARN] failed to decode data URI -> {out_path.name}: {e}")
        return None


def guess_ext_from_url_or_response(url: str, content_type: str | None) -> str:
    if content_type:
        ext = safe_ext_from_mime(content_type.split(";")[0].strip())
        if ext != "bin":
            return ext

    path = urlparse(url).path.lower()
    for ext in [".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".bmp", ".avif"]:
        if path.endswith(ext):
            return "jpg" if ext == ".jpeg" else ext.lstrip(".")
    return "bin"


def download_url_to_file(url: str, out_dir: Path, filename_stem: str) -> str | None:
    headers = {"User-Agent": USER_AGENT}
    try:
        r = requests.get(url, timeout=REQUEST_TIMEOUT, headers=headers)
        r.raise_for_status()

        ext = guess_ext_from_url_or_response(url, r.headers.get("Content-Type"))
        filename = f"{filename_stem}.{ext}"
        out_path = out_dir / filename
        out_path.write_bytes(r.content)
        return filename
    except Exception as e:
        print(f"[WARN] failed to download {url}: {e}")
        return None


def parse_srcset_first_url(srcset: str) -> str:
    if not srcset:
        return ""
    first = srcset.split(",")[0].strip()
    parts = first.split()
    return parts[0] if parts else ""


def choose_best_image_source(tag) -> tuple[str, str]:
    candidates = [
        tag.get("data-src", ""),
        tag.get("data-lazy-src", ""),
        tag.get("data-orig-file", ""),
        tag.get("currenturl", ""),
        parse_srcset_first_url(tag.get("srcset", "")),
        tag.get("src", ""),
    ]

    for c in candidates:
        if c.startswith("http://") or c.startswith("https://"):
            return "url", c

    for c in candidates:
        if c.startswith("data:") and ";base64," in c:
            return "data", c

    return "", ""


def rewrite_css_data_uris(css_text: str, common_counter_start: int) -> tuple[str, int]:
    """
    docs/css/style.css から docs/assets/common/... を参照するので
    相対パスは ../assets/common/...
    """
    counter = common_counter_start
    pattern = re.compile(r'url\((["\']?)(data:image/[^)"\']+;base64,[^)"\']+)\1\)', re.IGNORECASE)

    def repl(match):
        nonlocal counter
        data_uri = match.group(2)
        filename = save_data_uri_to_file(data_uri, COMMON_DIR, f"common_{counter:03d}")
        if filename:
            counter += 1
            return f'url("../assets/common/{filename}")'
        return match.group(0)

    rewritten = pattern.sub(repl, css_text)
    return rewritten, counter


def append_css(css_text: str, page_name: str, common_counter_start: int) -> int:
    css_text = css_text.strip()
    if not css_text:
        return common_counter_start

    rewritten, common_counter_end = rewrite_css_data_uris(css_text, common_counter_start)

    with STYLE_FILE.open("a", encoding="utf-8") as f:
        f.write(f"\n\n/* ===== extracted from {page_name} ===== */\n")
        f.write(rewritten)
        f.write("\n")

    return common_counter_end


def ensure_stylesheet_link(soup: BeautifulSoup) -> None:
    if soup.head is None:
        head_tag = soup.new_tag("head")
        if soup.html:
            soup.html.insert(0, head_tag)

    for link in soup.find_all("link", rel="stylesheet"):
        href = link.get("href", "")
        if href.endswith("../css/style.css"):
            return

    # docs/pages/*.html -> docs/css/style.css
    link_tag = soup.new_tag("link", rel="stylesheet", href="../css/style.css")
    soup.head.append(link_tag)


def add_archive_notice(soup: BeautifulSoup) -> None:
    if not soup.body:
        return

    note_html = f"""
<div class="notice">
  <p><strong>Archive note:</strong> This page is preserved as a historical mirror of a deleted web page for research and reference purposes.</p>
  <p>Original source: {ORIGINAL_SOURCE_NAME}</p>
  <p>No claim of authorship. Formatting may differ from the original.</p>
</div>
"""
    note_fragment = BeautifulSoup(note_html, "html.parser")
    soup.body.insert(0, note_fragment)


def process_head_assets(soup: BeautifulSoup, common_counter_start: int) -> tuple[int, list[str]]:
    counter = common_counter_start
    found_urls = []

    for link in soup.find_all("link"):
        rel_values = link.get("rel", [])
        href = link.get("href", "")

        rel_joined = " ".join(rel_values).lower() if isinstance(rel_values, list) else str(rel_values).lower()
        is_icon_like = any(key in rel_joined for key in ["icon", "apple-touch-icon", "mask-icon"])

        if not is_icon_like:
            continue

        if href.startswith("data:") and ";base64," in href:
            filename = save_data_uri_to_file(href, COMMON_DIR, f"icon_{counter:03d}")
            if filename:
                link["href"] = f"../assets/common/{filename}"
                counter += 1

        elif href.startswith("http://") or href.startswith("https://"):
            found_urls.append(href)
            filename = download_url_to_file(href, COMMON_DIR, f"icon_{counter:03d}")
            if filename:
                link["href"] = f"../assets/common/{filename}"
                counter += 1

    return counter, found_urls


def extract_title(soup: BeautifulSoup, fallback: str) -> str:
    if soup.title and soup.title.get_text(strip=True):
        return soup.title.get_text(strip=True)
    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        return h1.get_text(strip=True)
    return fallback


def copy_original_html(src_path: Path) -> None:
    shutil.copy2(src_path, ORIGINAL_DIR / src_path.name)


def process_inline_style_attributes(soup: BeautifulSoup, common_counter_start: int) -> int:
    """
    docs/pages/*.html から docs/assets/common/... を参照するので
    相対パスは ../assets/common/...
    """
    counter = common_counter_start
    style_url_pattern = re.compile(r'url\((["\']?)(data:image/[^)"\']+;base64,[^)"\']+)\1\)', re.IGNORECASE)

    for tag in soup.find_all(style=True):
        style_value = tag.get("style", "")

        def repl(match):
            nonlocal counter
            data_uri = match.group(2)
            filename = save_data_uri_to_file(data_uri, COMMON_DIR, f"common_{counter:03d}")
            if filename:
                counter += 1
                return f'url("../assets/common/{filename}")'
            return match.group(0)

        new_style = style_url_pattern.sub(repl, style_value)
        tag["style"] = new_style

    return counter


def process_page(src_path: Path, common_counter_start: int) -> tuple[int, dict]:
    page_name = src_path.stem
    out_html_path = PAGES_DIR / f"{page_name}.html"
    page_asset_dir = ASSETS_DIR / page_name
    page_asset_dir.mkdir(parents=True, exist_ok=True)

    copy_original_html(src_path)

    html = src_path.read_text(encoding="utf-8", errors="ignore")
    soup = BeautifulSoup(html, "html.parser")

    page_meta = {
        "page_name": page_name,
        "title": "",
        "source_urls": [],
    }

    common_counter = common_counter_start

    # <style> を docs/css/style.css に寄せる
    styles = soup.find_all("style")
    for style_tag in styles:
        common_counter = append_css(style_tag.get_text("\n"), page_name, common_counter)
        style_tag.decompose()

    # head assets
    common_counter, head_urls = process_head_assets(soup, common_counter)
    page_meta["source_urls"].extend(head_urls)

    # inline style の data:image
    common_counter = process_inline_style_attributes(soup, common_counter)

    # CSS link
    ensure_stylesheet_link(soup)

    img_index = 1

    # img
    for img in soup.find_all("img"):
        kind, value = choose_best_image_source(img)

        if kind == "data":
            filename = save_data_uri_to_file(value, page_asset_dir, f"{img_index:03d}")
            if filename:
                img["src"] = f"../assets/{page_name}/{filename}"
                img_index += 1

        elif kind == "url":
            page_meta["source_urls"].append(value)
            filename = download_url_to_file(value, page_asset_dir, f"{img_index:03d}")
            if filename:
                img["src"] = f"../assets/{page_name}/{filename}"
                img_index += 1

        elif img.get("src", "").startswith("file:///"):
            img["src"] = ""

        for attr in ["data-src", "data-lazy-src", "data-orig-file", "currenturl", "srcset"]:
            if attr in img.attrs:
                del img.attrs[attr]

    # source
    for tag in soup.find_all("source"):
        kind, value = choose_best_image_source(tag)

        if kind == "data":
            filename = save_data_uri_to_file(value, page_asset_dir, f"{img_index:03d}")
            if filename:
                tag["src"] = f"../assets/{page_name}/{filename}"
                if "srcset" in tag.attrs:
                    del tag.attrs["srcset"]
                img_index += 1

        elif kind == "url":
            page_meta["source_urls"].append(value)
            filename = download_url_to_file(value, page_asset_dir, f"{img_index:03d}")
            if filename:
                tag["src"] = f"../assets/{page_name}/{filename}"
                if "srcset" in tag.attrs:
                    del tag.attrs["srcset"]
                img_index += 1

    add_archive_notice(soup)

    page_meta["title"] = extract_title(soup, page_name)

    out_html_path.write_text(str(soup), encoding="utf-8")
    print(f"[OK] {src_path.name} -> {out_html_path}")

    return common_counter, page_meta


def write_meta_files(page_metas: list[dict]) -> None:
    source_lines = []
    for meta in page_metas:
        source_lines.append(f"# {meta['page_name']} | {meta['title']}")
        urls = sorted(set(u for u in meta["source_urls"] if u))
        source_lines.extend(urls)
        source_lines.append("")

    (META_DIR / "source_urls.txt").write_text("\n".join(source_lines), encoding="utf-8")

    capture_text = (
        f"Archive build date: {datetime.now().isoformat(timespec='seconds')}\n"
        f"Source directory: {SOURCE_DIR.resolve()}\n"
        "Input type: SingleFile HTML\n"
        "Output root: docs/\n"
    )
    (META_DIR / "capture_date.txt").write_text(capture_text, encoding="utf-8")

    (META_DIR / "notes.txt").write_text(NOTES_TEXT, encoding="utf-8")


def generate_index(page_metas: list[dict]) -> None:
    items = []
    for meta in sorted(page_metas, key=lambda x: x["page_name"]):
        items.append(
            f'<li><a href="pages/{meta["page_name"]}.html">{meta["title"]}</a>'
            f' <span class="meta">({meta["page_name"]})</span></li>'
        )

    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{ARCHIVE_TITLE}</title>
<link rel="stylesheet" href="css/style.css">
</head>
<body>

<h1>{ARCHIVE_TITLE}</h1>
<p>{ARCHIVE_SUBTITLE}</p>
<p class="meta">Original source: {ORIGINAL_SOURCE_NAME}</p>

<ul class="page-list">
{chr(10).join(items)}
</ul>

<hr>

<p class="meta">
This site preserves deleted pages for research and historical reference.<br>
No claim of authorship. Formatting may differ from the original.
</p>

</body>
</html>
"""
    INDEX_FILE.write_text(html, encoding="utf-8")


def main() -> None:
    ensure_dirs()

    html_files = sorted(SOURCE_DIR.glob("*.html"))
    if not html_files:
        print(f"[ERROR] no html files found in: {SOURCE_DIR.resolve()}")
        return

    page_metas = []
    common_counter = 1

    for src_path in html_files:
        common_counter, page_meta = process_page(src_path, common_counter)
        page_metas.append(page_meta)

    write_meta_files(page_metas)
    generate_index(page_metas)

    print("\nFinished.")
    print("Docs root:", DOCS_DIR.resolve())


if __name__ == "__main__":
    main()