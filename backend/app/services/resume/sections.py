"""Recognize section headings in 104-style PDFs without guessing field values."""

import re
from dataclasses import dataclass

_HEADINGS = {
    "個人資料": "基本資料", "基本資料": "基本資料",
    "工作經歷": "工作經歷", "工作經驗": "工作經歷",
    "學歷": "學歷", "求職條件": "求職條件", "語言能力": "語言能力",
    "專長": "技能與專長", "技能專長": "技能與專長", "技能": "技能與專長",
    "證照": "證照", "自傳": "自傳", "附件": "附件",
    "作品集": "作品集", "專案成就": "專案", "專案": "專案",
    "portfolio": "作品集", "experiencehighlights": "經歷摘要",
    "independentprojects": "專案", "currentstudy": "進修",
}
FIELD_GUIDE = """
輸入中的【第 n 頁｜段落】是本機依標題辨識的參考邊界。延續前頁的段落會在新頁再次標示。
- 工作經歷：同一筆內配對公司、職稱、起訖年月、工作內容與成果。保留 PDF 已擷取的順序，
  不把另一欄、另一筆經歷或專案的日期借給這一筆。只列年月或只有起日，就保留實際提供的精度。
- 學歷：同一筆內配對學校、科系、學位與就讀時間。求職條件只取本人明確填的條件。
- 「經歷摘要」是履歷自述摘要；沒有明列公司或職稱時，不拆成獨立經歷。「進修」不推定已取得學位。
- 技能與專長、語言能力、證照、專案與作品集各填對應欄位。工具列表不能當成經歷成果；
  只有文字明確連到某份經歷或專案，才建立 linked_ids。
- 逐段檢查「技能與專長」、工作成果、專案和自傳，保留明列的不同技能與工具；同名重複項合併。
  專案主題相同時，合併各處明列的說明、技術與成果。只有可確定與該專案相連的 [URL-n] 才填 projects[].url。
- 未填的可空欄位回 null；未填的清單回 []。必要識別欄位不足以建立一筆時，暫不建立該筆，
  並在 missing_flags 指出待確認的段落。不要以其他段落的值補猜。
- source_text 只能填同一筆原文中可找到的片段。缺少可核對的片段就填 null。
""".strip()


@dataclass(frozen=True)
class Section:
    page: int
    name: str
    text: str


def find_sections(pages: list[str]) -> list[Section]:
    """Attach page and section labels while keeping the PDF's extraction order."""
    sections = []
    seen = set()
    current = "未分類"
    for number, page in enumerate(pages, 1):
        lines = []
        for line in page.splitlines():
            key = re.sub(r"\s+", "", line).rstrip("：:").strip("【】").casefold()
            heading = _HEADINGS.get(key)
            if heading is None:
                lines.append(line)
                continue
            if any(line.strip() for line in lines):
                sections.append(Section(number, current, "\n".join(lines)))
            current = heading
            seen.add(heading)
            lines = []
        if any(line.strip() for line in lines):
            sections.append(Section(number, current, "\n".join(lines)))
    # Match headings used by this layout, not any one word in a generic document.
    return sections if {"工作經歷", "學歷"} <= seen else []


def fill_missing_fields(profile: dict) -> None:
    """Normalize a small set of explicitly nullable fields without inventing values."""
    for section, fields in (
        ("work_experience", ("start", "end")),
        ("education", ("field", "degree", "start", "end")),
        ("skills", ("category", "stated_level_text", "years_text")),
        ("projects", ("start", "end")),
    ):
        records = profile.get(section)
        if not isinstance(records, list):
            continue
        for record in records:
            if isinstance(record, dict):
                for field in fields:
                    value = record.get(field)
                    if value is None or (isinstance(value, str) and not value.strip()):
                        record[field] = None
