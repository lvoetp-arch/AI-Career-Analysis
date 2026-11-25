"""解析流程總入口（純函式；不碰資料庫與儲存，由路由層負責）。"""
from collections import Counter
from dataclasses import dataclass, field
import re
from typing import Callable

from .errors import ParseError
from .namemask import mask_names, parse_variants
from .parser import LLMClient, prune_dangling, structure_resume, validate_restored
from .pdf_text import extract
from .redact import redact
from .sections import Section, find_sections
from .urlmask import UrlMap, preserve_portfolio, restore_urls, tokenize_urls

_SECTION_MARKER = re.compile(r"^【第 \d+ 頁｜([^】]+)】\s*\n", re.MULTILINE)
_REVIEW_FIELDS = {"工作經歷": "work_experience", "學歷": "education", "技能與專長": "skills", "專案": "projects"}


def prepared_sections(prepared: str):
    markers = list(_SECTION_MARKER.finditer(prepared))
    for index, marker in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(prepared)
        yield marker[1], prepared[marker.end():end].strip()


def preserve_autobiography(profile: dict, prepared: str) -> None:
    """Use the cleaned document text for the original-text field."""
    pieces = [content for title, content in prepared_sections(prepared) if title == "自傳"]
    if pieces:
        profile["autobiography"]["text"] = "\n\n".join(pieces)


def flag_empty_sections(profile: dict, prepared: str) -> None:
    flags = profile["missing_flags"]
    flagged = {flag["field_path"] for flag in flags}
    for title in dict.fromkeys(_SECTION_MARKER.findall(prepared)):
        field = _REVIEW_FIELDS.get(title)
        if field and not profile.get(field) and field not in flagged:
            flags.append({
                "field_path": field, "reason": "missing",
                "message": f"讀取到「{title}」段落，但沒有整理出項目。請核對原始履歷，並視需要補填。",
            })


@dataclass
class ParseOutcome:
    ok: bool
    profile: dict | None = None
    error_code: str | None = None
    user_message: str | None = None
    notes: list[str] = field(default_factory=list)
    attempts: int = 0


def prepare_text(text: str, links: list[str], name_raw: str | None, *, sections: list[Section] | None = None) -> tuple[str, UrlMap, dict[str, int]]:
    """遮蔽順序：規則式個資（含 Email、社群網址）→ 其餘網址代號化 → 姓名。"""
    if sections:
        counts = Counter()
        blocks = []
        for section in sections:
            cleaned, removed = redact(section.text)
            counts.update(removed)
            if cleaned.strip():
                blocks.append(f"【第 {section.page} 頁｜{section.name}】\n{cleaned.strip()}")
        safe = "\n\n".join(blocks)
    else:
        safe, counts = redact(text)
    safe, umap = tokenize_urls(safe, links)
    safe, n = mask_names(safe, parse_variants(name_raw))
    if len(umap):
        counts["urls_tokenized"] = len(umap)
    if n:
        counts["name_masked"] = n
    return safe, umap, counts


def run_parse(
    pdf_bytes: bytes,
    llm: LLMClient,
    name_variants: str | None = None,
    *,
    deadline: float | None = None,
    before_llm: Callable[[], None] | None = None,
) -> ParseOutcome:
    """deadline：整個流程（含 Schema 不符後的第二次呼叫）共用的總時限，time.monotonic() 的絕對時間。
    before_llm：本機驗證（格式、大小、加密、頁數、是否有文字）全部通過後、第一次呼叫 LLM 之前執行一次；
    路由層用它來扣每日額度，所以本機驗證不通過的檔案不會扣額度。它丟出的例外（如 RATE_LIMITED）不會被吞掉。"""
    try:
        ex = extract(pdf_bytes)
        sections = find_sections(ex.pages)
        safe, umap, counts = prepare_text(ex.text, ex.links, name_variants, sections=sections)
        if before_llm is not None:
            before_llm()
        profile, attempts, n0 = structure_resume(safe, llm, deadline, field_first=bool(sections))
        if sections:
            preserve_autobiography(profile, safe)
            preserve_portfolio(profile, prepared_sections(safe), umap)
            flag_empty_sections(profile, safe)
        profile, n1 = restore_urls(profile, umap)
        n2 = prune_dangling(profile)
        restored_truncated = validate_restored(profile)
        if restored_truncated:
            n1.append(f"restored_text_truncated={restored_truncated}")
        notes = ["unicode_selective_normalized", "pii_redacted_before_llm:" + ",".join(f"{k}={v}" for k, v in sorted(counts.items()))]
        if not name_variants:
            notes.append("name_masking_skipped_rule_based_only")
        notes += n0 + n1 + n2
        profile["parse_notes"] = notes
        return ParseOutcome(ok=True, profile=profile, notes=notes, attempts=attempts)
    except ParseError as e:
        return ParseOutcome(ok=False, error_code=e.code, user_message=e.user_message)
