"""步驟 3：規則式遮蔽（電話、Email、住址、身分證、個資標籤行、LINE ID、人口資料、民國生日、社群網址）。
回傳只含各類別次數，不含被遮蔽內容。"""
import re
from collections import Counter

MASK = "[已遮蔽]"

SENSITIVE_LABELS = [
    "主要手機", "手機", "行動電話", "聯絡電話", "電話", "E-mail", "Email", "電子郵件", "通訊地址", "地址", "戶籍地址",
    "聯絡方式", "英文姓名", "姓名", "性別", "生日", "出生年月日", "出生日期", "年齡", "婚姻", "婚姻狀況", "役別",
    "兵役", "身分證", "身分證字號", "國籍", "宗教", "就業狀態", "交通工具", "健康狀況", "照片",
    "LINE ID", "LINE", "Line", "Line ID", "微信", "WeChat",
]
_ALT = "|".join(re.escape(x) for x in sorted(SENSITIVE_LABELS, key=len, reverse=True))
_LABEL_INLINE = re.compile(rf"^\s*(?:{_ALT})\s*[:：]\s*\S.*$", re.I)
_LABEL_ONLY = re.compile(rf"^\s*(?:{_ALT})\s*[:：]?\s*$", re.I)
# 104 表格式「性別 女」（無冒號、同行）
_LABEL_SPACE = re.compile(rf"^\s*(?:性別|年齡|婚姻|婚姻狀況|役別|兵役|就業狀態|交通工具|出生年月日|生日)\s+\S.*$")

# 整個片段就是人口資料才刪（用 ｜ | 、 , 分隔的片段）
_SEG_SPLIT = re.compile(r"\s*[｜|、,，]\s*")
_SEG_DEMO = re.compile(
    r"^(?:性別[:：]?\s*)?(?:男|女|男性|女性)$"
    r"|^\d{1,2}\s*歲$"
    r"|^(?:未婚|已婚|離婚|單身|喪偶)$"
    r"|^民國\s*\d{1,3}\s*年\s*生$"
    r"|^(?:西元\s*)?\d{4}\s*年\s*生$"
    r"|^(?:役畢|免役|未役|替代役|義務役)\S*$"
    r"|^(?:役別|兵役)[:：]?\S*$"
)

_PATTERNS = {
    "email": re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"),
    "tw_id": re.compile(r"(?<![A-Za-z0-9])[A-Z][12]\d{8}(?![A-Za-z0-9])"),
    "mobile": re.compile(r"(?<!\d)09\d{2}[-\s]?\d{3}[-\s]?\d{3}(?!\d)"),
    "landline": re.compile(r"(?<!\d)\(?0\d{1,2}\)?[-\s]?\d{3,4}[-\s]?\d{4}(?!\d)"),
    # 縣市＋區鄉鎮＋路街（PDF 會把地址從中間折行，所以允許換行）；只有「台中市太平區」不會被遮
    "address": re.compile(
        r"[\u4e00-\u9fff]{2,3}[縣市](?:[\u4e00-\u9fff]|\n){1,4}?[區鄉鎮市](?:[\u4e00-\u9fff0-9]|\n){1,10}?(?:路|街|大道)"
        r"(?:[ \t]?\n?[0-9０-９]{1,4}(?:[-－]\d{1,3})?[巷弄號樓之Ff]{0,1}){0,6}[號樓Ff]{0,2}"
    ),
    "line_id": re.compile(r"(?i)\bline\s*(?:id)?\s*[:：]\s*[A-Za-z0-9._\-@]{3,40}"),
    "roc_birth": re.compile(r"民國\s*\d{1,3}\s*年\s*(?:\d{1,2}\s*月\s*(?:\d{1,2}\s*日)?\s*)?(?:出)?生"),
    "birth_date": re.compile(r"出生(?:年月日|日期)?\s*[:：]?\s*(?:民國|西元)?\s*\d{2,4}\s*[年/\-.]\s*\d{1,2}\s*[月/\-.]?\s*(?:\d{1,2}\s*日?)?"),
    "social_url": re.compile(
        r"(?i)(?:https?://)?(?:[a-z0-9\-]+\.)*(?:linkedin\.com|facebook\.com|fb\.com|instagram\.com|threads\.net|x\.com|twitter\.com|"
        r"line\.me|t\.me|wechat\.com)(?:/[^\s｜|，,）)]*)?"
    ),
    # 單獨出現的年齡；排除「70 歲以下客群」「25～35 歲」這類內文
    "age_inline": re.compile(r"(?<![\d～~\-至到])(?<!\d\s)\b\d{2}\s*歲(?!以|至|到|～|~|的客|族|客|層|左右|前|後|女|男|用戶|使用者|學員)"),
}
_ORDER = ["social_url", "email", "line_id", "tw_id", "mobile", "landline", "birth_date", "roc_birth", "address", "age_inline"]


def _clean_segments(line: str, counts: Counter) -> str | None:
    """行內以 ｜ 、 分隔時，只刪整段都是人口資料的片段。全刪光就回傳 None（整行丟棄）。"""
    if not _SEG_SPLIT.search(line):
        return line
    seps = _SEG_SPLIT.findall(line)
    parts = _SEG_SPLIT.split(line)
    keep = []
    for i, p in enumerate(parts):
        if _SEG_DEMO.match(p.strip()):
            counts["demographic_segment"] += 1
            continue
        keep.append((p, seps[i - 1] if i > 0 else ""))
    if len(keep) == len(parts):
        return line
    if not keep:
        return None
    return "".join((s if j else "") + p for j, (p, s) in enumerate(keep))


def redact(text: str) -> tuple[str, dict[str, int]]:
    counts: Counter[str] = Counter()
    kept, skip_next = [], False
    for line in text.split("\n"):
        if skip_next:
            skip_next = False
            counts["label_value"] += 1
            continue
        if _LABEL_INLINE.match(line) or _LABEL_SPACE.match(line):
            counts["label_inline"] += 1
            continue
        if _LABEL_ONLY.match(line):
            counts["label_line"] += 1
            skip_next = True
            continue
        cleaned = _clean_segments(line, counts)
        if cleaned is None:
            counts["demographic_line"] += 1
            continue
        kept.append(cleaned)
    out = "\n".join(kept)
    for name in _ORDER:
        out, n = _PATTERNS[name].subn(MASK, out)
        if n:
            counts[name] += n
    return out, dict(counts)
