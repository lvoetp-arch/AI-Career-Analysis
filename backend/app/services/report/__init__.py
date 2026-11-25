"""Project readable evidence labels without exposing the analysis snapshot."""

import re
from urllib.parse import urlsplit

from app.services.analysis.result import organization_aliases, original_role_keys, resolve_evidence_path, role_key, valid_reference
from app.services.resume.redact import redact

_LABELS = {
    "basics": "履歷摘要",
    "work_experience": "工作經歷",
    "education": "學歷",
    "skills": "履歷技能",
    "projects": "專案",
    "certifications": "證照",
    "languages": "語言",
    "portfolio": "作品集",
    "autobiography": "自傳",
    "job_preferences": "履歷求職偏好",
}
_NAMED_FIELDS = {
    "work_experience": ("company", "title"),
    "education": ("school", "degree"),
    "skills": ("name",),
    "projects": ("title", "name"),
    "certifications": ("name",),
    "languages": ("language",),
    "portfolio": ("title",),
}
_ROOT = re.compile(r"^([a-z_]+)(?:\[([0-9]{1,6})\])?(?:\.|$)")
_RATING = re.compile(r"^career_input\.skill_ratings\[([0-9]{1,6})\](?:\.|$)")
_EXCERPT_FIELDS = (
    "title", "name", "company", "school", "headline", "degree", "text",
    "responsibilities", "achievements", "description", "outcomes", "rating",
)
_EVIDENCE_MARKER = r"E-[0-9a-f]{12}"
_EVIDENCE_PARENS = re.compile(
    rf"[ \t]*[(（\[【]\s*{_EVIDENCE_MARKER}(?:[\s,，、/]+{_EVIDENCE_MARKER})*\s*[)）\]】]",
    re.IGNORECASE,
)
_INLINE_EVIDENCE = re.compile(rf"(?<![a-z0-9_-]){_EVIDENCE_MARKER}(?![a-z0-9_-])", re.IGNORECASE)
_ORGANIZATION_ALIAS = re.compile(r"(公司|學校)[-－]([0-9]+)")


def report_result(result: dict | None, snapshot: dict) -> dict | None:
    """Present saved reports using readable names and the report's existing evidence links."""
    names = {alias: name for name, alias in organization_aliases(snapshot).items()}

    def present(value):
        if isinstance(value, dict):
            return {key: present(item) for key, item in value.items()}
        if isinstance(value, list):
            return [present(item) for item in value]
        if isinstance(value, str):
            value = _EVIDENCE_PARENS.sub("", value)
            value = _INLINE_EVIDENCE.sub("履歷資料", value)
            return _ORGANIZATION_ALIAS.sub(lambda match: names.get(f"{match[1]}-{match[2]}", match[1]), value)
        return value

    return present(result)


def alternative_comparison(result: dict, snapshot: dict) -> dict | None:
    overview = result.get("career_overview") or {}
    if overview.get("alternative_strategy") == "skill_bridge":
        return None
    alternatives = overview.get("alternatives")
    if not isinstance(alternatives, list) or (alternatives and original_role_keys(snapshot)):
        return None

    directions = sorted(result.get("directions", []), key=lambda direction: direction["rank"])
    seen = {role_key(option["role_name"]) for option in [*directions[:1], *alternatives]}
    fallback_options = []
    for direction in directions[1:]:
        if len(alternatives) + len(fallback_options) >= 2:
            break
        key = role_key(direction["role_name"])
        if not key or key in seen:
            continue
        refs = [
            ref for claim in direction["fit_evidence"] if claim["strength"] in {"sufficient", "partial"}
            for ref in claim["evidence_refs"]
            if ref["source"] in {"resume", "autobiography"} and valid_reference(ref, snapshot)
        ]
        if not refs:
            continue
        gap = next(iter(direction["gaps"]), None)
        step = next(iter(direction["action_plan"].get("day_30", [])), {})
        fallback_options.append({
            "role_name": direction["role_name"],
            "transfer": direction["summary"],
            "gap": gap["why_it_matters"] if gap else next(iter(direction["risks"]), ""),
            "validation": step.get("action", ""),
            "success_criteria": step.get("success_criteria", ""),
            "evidence_refs": refs[:2],
        })
        seen.add(key)
    return {"fallback_options": fallback_options}


def _item_name(items, index: int, fields: tuple[str, ...]) -> str | None:
    if not isinstance(items, list) or index >= len(items) or not isinstance(items[index], dict):
        return None
    for field in fields:
        value = items[index].get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _label(ref: dict, snapshot: dict) -> str:
    source, path = ref["source"], ref["path"]
    if source == "self_rating":
        match = _RATING.match(path)
        career = snapshot.get("career_input") or {}
        name = _item_name(career.get("skill_ratings"), int(match[1]), ("name",)) if match else None
        return f"技能自評：{name}" if name else "技能自評"
    if source == "preference":
        return "求職偏好"
    if source == "autobiography":
        return "自傳"
    match = _ROOT.match(path)
    if not match:
        return "履歷"
    section, index = match.groups()
    category = _LABELS.get(section, "履歷")
    name = _item_name(snapshot.get(section), int(index), _NAMED_FIELDS.get(section, ())) if index else None
    return f"{category}：{name}" if name else category


def evidence_labels(result: dict, snapshot: dict) -> dict[str, dict[str, str]]:
    labels = {}
    claims = [claim for direction in result.get("directions", []) for claim in direction.get("fit_evidence", [])]
    alternatives = (result.get("career_overview") or {}).get("alternatives", [])
    skills = [skill for option in alternatives for skill in option.get("skill_bridge", {}).get("source_skills", [])]
    for item in [*claims, *alternatives, *skills]:
        for ref in item.get("evidence_refs", []):
            labels.setdefault(ref["source"], {})[ref["path"]] = _label(ref, snapshot)
    return labels


def evidence_excerpts(labels: dict[str, dict[str, str]], snapshot: dict) -> dict[str, dict[str, str]]:
    def render(value) -> str:
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, int) and not isinstance(value, bool):
            return str(value)
        if isinstance(value, list):
            return "；".join(part for item in value if isinstance(item, str) and (part := render(item)))
        if isinstance(value, dict):
            return "；".join(part for field in _EXCERPT_FIELDS if (part := render(value.get(field))))
        return ""

    excerpts = {}
    for source, paths in labels.items():
        for path in paths:
            try:
                excerpt, _ = redact(render(resolve_evidence_path(snapshot, path)))
            except (KeyError, IndexError):
                continue
            if excerpt:
                excerpts.setdefault(source, {})[path] = excerpt if len(excerpt) <= 220 else excerpt[:219] + "…"
    return excerpts


def evidence_links(labels: dict[str, dict[str, str]], snapshot: dict) -> dict[str, str]:
    links = {}
    for path in labels.get("resume", {}):
        match = _ROOT.match(path)
        if not match or match[1] not in {"portfolio", "projects"} or match[2] is None:
            continue
        try:
            value = resolve_evidence_path(snapshot, f"{match[1]}[{match[2]}].url")
            if isinstance(value, str):
                url = urlsplit(value)
                if url.scheme in {"https", "http"} and url.hostname:
                    links[path] = value
        except (KeyError, IndexError, ValueError):
            continue
    return links
