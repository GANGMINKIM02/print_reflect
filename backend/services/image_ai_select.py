from __future__ import annotations

"""Upstage Solar — 소제목 본문에 맞는 LEGAL_DB 그림 선택."""

import json
import re

from backend.config import settings
from backend.services import upstage
from backend.services.image_matcher import (
    _overlap_score,
    list_image_catalog,
    normalize_match_text,
)


def rank_catalog_candidates(
    section_text: str,
    used_files: set[str],
    *,
    limit: int = 40,
) -> list[dict[str, str]]:
    catalog = list_image_catalog()
    scored: list[tuple[int, dict[str, str]]] = []
    for item in catalog:
        image_file = item["image_file"]
        if image_file in used_files:
            continue
        score = _overlap_score(section_text, item["title"])
        scored.append((score, item))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    candidates = [item for score, item in scored if score > 0][:limit]
    if len(candidates) < min(12, limit):
        seen = {c["image_file"] for c in candidates}
        for item in catalog:
            if item["image_file"] in used_files or item["image_file"] in seen:
                continue
            candidates.append(item)
            seen.add(item["image_file"])
            if len(candidates) >= limit:
                break
    return candidates[:limit]


async def pick_image_with_upstage(
    section_text: str,
    candidates: list[dict[str, str]],
) -> str | None:
    if not candidates or settings.use_mock:
        return None

    lines = "\n".join(
        f'{idx + 1}. file="{item["image_file"]}" title="{item["title"]}"'
        for idx, item in enumerate(candidates)
    )
    system = (
        "당신은 발달장애인용 이지리드 판결문에 넣을 그림을 고르는 전문가입니다. "
        "주어진 후보 목록에서 본문 의미를 가장 잘 설명하는 그림 1개만 고르세요. "
        'JSON만 출력: {"image_file":"image_XXX.png"}'
    )
    user = (
        f"다음 소제목/본문을 대표하는 그림 1개를 고르세요.\n\n"
        f"[본문]\n{section_text[:2500]}\n\n"
        f"[후보]\n{lines}"
    )

    try:
        raw = await upstage.chat_completion(system, user, max_tokens=300, temperature=0.1)
    except Exception:
        return None

    match = re.search(r"\{[^{}]*\}", raw, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group())
    except json.JSONDecodeError:
        return None

    image_file = str(data.get("image_file", "")).strip()
    allowed = {item["image_file"] for item in candidates}
    if image_file in allowed:
        return image_file

    # 모델이 파일명만 텍스트로 준 경우
    for token in re.findall(r"image_\d+\.png", raw):
        if token in allowed:
            return token
    return None


def candidate_title(candidates: list[dict[str, str]], image_file: str) -> str | None:
    for item in candidates:
        if item["image_file"] == image_file:
            return item["title"]
    return None
