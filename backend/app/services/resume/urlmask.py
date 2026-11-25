"""步驟 5：網址代號化（契約 §10-12a）。送 LLM 前換成 [URL-n]，結果回來後由程式還原。"""
import re
from collections.abc import Iterable
from dataclasses import dataclass, field

_URL = re.compile(
    r"(?i:https?://|www\.)[^\s｜|，,；;。！？）)〉》」』】\]]+"
    r"|\b[a-z0-9][a-z0-9\-]*(?:\.[a-z0-9\-]+)*\.(?:com|net|org|io|dev|me|app|tv|tw|co)(?:[/?#][^\s｜|，,；;。！？）)〉》」』】\]]*)?(?![A-Za-z0-9])"
)
_TOKEN = re.compile(r"\[URL-(\d+)\]")
_ELLIPSIS = ("…", "⋯", "...")
_PORTFOLIO_LABEL = re.compile(r"[^\[\]]{0,40}(?:作品集|作品(?:連結|網址|展示)|專案(?:連結|網址|展示))")


def _norm(u: str) -> str:
    u = u.strip().rstrip("…⋯.")
    u = re.sub(r"^https?://", "", u, flags=re.I)
    u = re.sub(r"^www\.", "", u, flags=re.I)
    return u.rstrip("/").lower()


@dataclass
class UrlEntry:
    display: str
    full: str | None
    truncated: bool


@dataclass
class UrlMap:
    entries: dict[int, UrlEntry] = field(default_factory=dict)

    def __len__(self):
        return len(self.entries)


def tokenize_urls(text: str, links: list[str]) -> tuple[str, UrlMap]:
    umap = UrlMap()
    n = 0

    def repl(m: re.Match) -> str:
        nonlocal n
        disp = m.group(0)
        truncated = disp.endswith(_ELLIPSIS)
        key = _norm(disp)
        full = next((l for l in links if _norm(l) == key), None)
        if full is None and truncated and key:
            matches = {l for l in links if _norm(l).startswith(key)}
            if len(matches) == 1:
                full = matches.pop()
        if full is None and not truncated:
            full = disp if disp.lower().startswith(("https://", "http://")) else f"https://{disp}"
        n += 1
        umap.entries[n] = UrlEntry(disp, full, truncated and full is None)
        return f"[URL-{n}]"

    return _URL.sub(repl, text), umap


def _clean_str(s: str, umap: UrlMap, as_url: bool):
    """as_url：整個欄位應為網址。回傳字串或 None。"""
    m = _TOKEN.fullmatch(s.strip())
    if as_url and m:
        e = umap.entries.get(int(m.group(1)))
        return e.full if e and e.full else None
    return _TOKEN.sub(lambda mm: (umap.entries[int(mm.group(1))].display if int(mm.group(1)) in umap.entries else ""), s)


def _item_url_entry(item: dict) -> int | None:
    url = item.get("url")
    if isinstance(url, str) and (match := _TOKEN.fullmatch(url.strip())):
        return int(match[1])
    if url:
        return None

    tokens = {
        int(token)
        for field in ("source_text", "description", "title", "name")
        for token in _TOKEN.findall(item.get(field) or "")
    }
    return next(iter(tokens)) if len(tokens) == 1 else None


def preserve_portfolio(profile: dict, sections: Iterable[tuple[str, str]], umap: UrlMap) -> None:
    """Keep explicitly labeled portfolio links even when the model omits them."""
    candidates = {}
    for section, body in sections:
        if section not in {"作品集", "附件"}:
            continue
        lines = [line.strip() for line in body.splitlines() if line.strip()]
        for index, line in enumerate(lines):
            for token in _TOKEN.finditer(line):
                label = line[:token.start()].strip(" ：:-") or line[token.end():].strip(" ：:-")
                if not label and index + 1 < len(lines):
                    label = lines[index + 1]
                title = label if _PORTFOLIO_LABEL.fullmatch(label) else None
                if section == "附件" and title is None:
                    continue
                entry = umap.entries.get(int(token[1]))
                if entry is None or (entry.full is None and not entry.truncated):
                    continue
                key = entry.full or entry.display
                if key not in candidates or (title and candidates[key][0] is None):
                    candidates[key] = (title, token[0])

    if not candidates:
        return
    portfolio = profile["portfolio"]
    used_ids = {item["id"] for item in portfolio}
    # Compare resolved destinations so repeated mentions of the same work stay one item.
    represented = set()
    for item in portfolio:
        if entry := _item_url_entry(item):
            url = umap.entries.get(entry)
            if url:
                represented.add(url.full or url.display)
        elif item.get("url"):
            represented.add(item["url"])

    index = 1
    for key, (title, token) in candidates.items():
        if key in represented:
            continue
        matching = [item for item in portfolio if title and item["title"] == title and not item.get("url")]
        if len(matching) == 1:
            matching[0]["url"] = token
            continue
        while f"portfolio-{index}" in used_ids:
            index += 1
        portfolio.append({
            "id": f"portfolio-{index}",
            "title": title or "作品集",
            "url": token,
            "source_text": f"{title} {token}" if title else token,
        })
        index += 1


def restore_urls(profile: dict, umap: UrlMap) -> tuple[dict, list[str]]:
    notes: list[str] = []
    flags = profile.setdefault("missing_flags", [])

    def flag(path, msg):
        if not any(f["field_path"] == path and f["reason"] == "truncated" for f in flags):
            flags.append({"field_path": path, "reason": "truncated", "message": msg})

    for sec in ("portfolio", "projects"):
        for i, item in enumerate(profile.get(sec, [])):
            e = umap.entries.get(_item_url_entry(item))
            if e is None and sec == "projects" and "url" not in item:
                continue
            cur = item.get("url")
            if isinstance(cur, str):
                cur = _clean_str(cur, umap, True) if _TOKEN.search(cur) else cur
            if not cur and e:
                cur = e.full
            item["url"] = cur
            trunc = bool(e and e.truncated and not cur)
            if sec == "portfolio":
                item["url_truncated"] = trunc
            if trunc:
                label = "作品集" if sec == "portfolio" else "專案"
                flag(f"{sec}[{i}].url", f"{label}網址在履歷上被截斷，請手動補上完整網址。")
                notes.append("truncated_url_unresolved")
            elif e and cur and e.full:
                notes.append("url_restored")
    # basics.links：url 必填，還原不了就移除該筆
    links = profile.get("basics", {}).get("links", [])
    if links:
        keep = []
        for it in links:
            u = it.get("url", "")
            u = _clean_str(u, umap, True) if _TOKEN.search(u) else u
            if u:
                it["url"] = u
                keep.append(it)
        profile["basics"]["links"] = keep

    # 其餘字串：殘留 [URL-n] 還原為顯示文字，對不上的清除
    def walk(o):
        if isinstance(o, dict):
            return {k: (walk(v) if not (k == "url" and isinstance(v, str)) else v) for k, v in o.items()}
        if isinstance(o, list):
            return [walk(x) for x in o]
        if isinstance(o, str) and _TOKEN.search(o):
            return _clean_str(o, umap, False)
        return o

    profile = walk(profile)
    return profile, notes
