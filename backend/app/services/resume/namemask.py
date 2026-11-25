"""步驟 4：姓名遮蔽（契約 §10-1）。只在記憶體中處理，不儲存、不記錄。"""
import re

MAX_VARIANTS = 3
PLACEHOLDER = "[姓名]"
_CJK = re.compile(r"[\u3400-\u9fff]")


def parse_variants(raw: str | None) -> list[str]:
    if not raw:
        return []
    parts = [p.strip() for p in re.split(r"[,，、\n]", raw) if p.strip()]
    seen, out = set(), []
    for p in parts:
        if p.lower() not in seen:
            seen.add(p.lower())
            out.append(p[:40])
    return out[:MAX_VARIANTS]


def _pattern(name: str) -> str:
    # 中文字之間、英文單字之間允許空白（PDF 排版常見）
    toks = [re.escape(c) for c in name if not c.isspace()] if _CJK.search(name) else [re.escape(w) for w in name.split()]
    return (r"\s*" if _CJK.search(name) else r"\s+").join(toks)


def mask_names(text: str, variants: list[str]) -> tuple[str, int]:
    total = 0
    for v in sorted(variants, key=len, reverse=True):
        pat = _pattern(v)
        if not pat:
            continue
        if not _CJK.search(v):
            pat = rf"(?<![A-Za-z0-9]){pat}(?![A-Za-z0-9])"
        text, n = re.subn(pat, PLACEHOLDER, text, flags=re.I)
        total += n
    return text, total
