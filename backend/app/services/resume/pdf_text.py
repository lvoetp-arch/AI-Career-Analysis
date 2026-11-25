"""步驟 1–2：驗證 PDF、抽文字與連結註記、選擇性字元正規化（不用完整 NFKC）。"""
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field

import pymupdf

from .errors import ParseError

MAX_BYTES = 5 * 1024 * 1024
MAX_PAGES = 10
MAX_CHARS = 40_000
MIN_CHARS = 80


@dataclass
class Extracted:
    text: str
    links: list[str] = field(default_factory=list)
    page_count: int = 0
    pages: list[str] = field(default_factory=list)


def normalize_text(text: str) -> str:
    """只轉康熙部首／部首補充／相容漢字。全形標點與「…」保持原樣。"""
    out = []
    for ch in text:
        cp = ord(ch)
        if 0x2E80 <= cp <= 0x2FDF or 0xF900 <= cp <= 0xFAFF:
            ch = unicodedata.normalize("NFKC", ch)
        out.append(ch)
    return "".join(out).replace("\u200b", "")


def extract(pdf_bytes: bytes) -> Extracted:
    if len(pdf_bytes) > MAX_BYTES or not pdf_bytes.startswith(b"%PDF"):
        raise ParseError("FILE_INVALID")
    try:
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    except Exception:
        raise ParseError("FILE_INVALID")
    if doc.is_encrypted or doc.needs_pass or doc.page_count == 0 or doc.page_count > MAX_PAGES:
        raise ParseError("FILE_INVALID")
    pages, links = [], []
    visible_chars = 0
    for page in doc:
        visible = normalize_text(page.get_text())
        visible_chars += sum(not char.isspace() for char in visible)
        lines = visible.splitlines(keepends=True)
        positions = defaultdict(list)
        for index, line in enumerate(lines):
            positions[line.strip()].append(index)
        labeled_links = defaultdict(set)
        for link in page.get_links():
            uri = link.get("uri")
            if link.get("kind") != pymupdf.LINK_URI or not uri or not re.fullmatch(r"https?://[^\s]+", uri, re.I):
                continue
            if uri not in links:
                links.append(uri)
            label = normalize_text(page.get_text(clip=link["from"])).strip()
            if label and len(positions[label]) == 1 and not re.search(r"https?://|www\.|\.[a-z]{2,}(?:[/?#]|$)|…|⋯|\.\.\.", label, re.I):
                labeled_links[positions[label][0]].add(uri)
        for index, destinations in labeled_links.items():
            if len(destinations) == 1:
                line = lines[index]
                body = line.rstrip("\r\n")
                lines[index] = f"{body} {next(iter(destinations))}{line[len(body):]}"
        pages.append("".join(lines))
    text = "\n".join(pages).strip()
    if visible_chars < MIN_CHARS:
        raise ParseError("SCANNED_OR_EMPTY")
    if len(text) > MAX_CHARS:
        raise ParseError("FILE_INVALID")
    return Extracted(text=text, links=links, page_count=doc.page_count, pages=pages)
