from __future__ import annotations

"""챗봇 — LEGAL_DB·문서 맥락 우선, 부족 시 웹 검색 후 Solar 응답."""

import sys
from pathlib import Path

from rapidfuzz import fuzz, process

from backend.config import ROOT_DIR
from backend.database import get_document
from backend.models.schemas import ChatMessage, ChatResponse
from backend.services import upstage
from backend.services.prompts import load_chatbot_prompt
from backend.services.web_search import search_web

sys.path.insert(0, str(ROOT_DIR))
from db_rules import LEGAL_DB  # noqa: E402

NEED_WEB_MARKER = "NEED_WEB_SEARCH"
DB_MATCH_THRESHOLD = 55
DB_RESULT_LIMIT = 6


def search_legal_db(query: str, *, limit: int = DB_RESULT_LIMIT) -> list[dict[str, str]]:
    """LEGAL_DB에서 질의와 관련된 이지리드 사례 검색."""
    if not query.strip() or not LEGAL_DB:
        return []

    matches = process.extract(
        query,
        LEGAL_DB.keys(),
        scorer=fuzz.token_set_ratio,
        limit=limit,
    )

    hits: list[dict[str, str]] = []
    seen: set[str] = set()
    for key, score, _ in matches:
        if score < DB_MATCH_THRESHOLD:
            continue
        if key in seen:
            continue
        seen.add(key)
        entries = LEGAL_DB.get(key) or []
        if not entries:
            continue
        entry = entries[0]
        hits.append(
            {
                "original": key,
                "easy_text": (entry.get("easy_text") or "").strip(),
                "title": (entry.get("title") or "").strip(),
                "score": str(score),
            }
        )
    return hits


async def build_document_context(doc_id: str | None) -> str:
    if not doc_id:
        return ""
    doc = await get_document(doc_id)
    if not doc:
        return ""

    parts = [
        f"파일명: {doc.filename}",
        f"사건 유형: {doc.doc_type}",
        f"진행 단계: {doc.stage}",
    ]
    if doc.summary:
        parts.append(f"요약:\n{doc.summary[:4000]}")
    translation = doc.translation_text
    if not translation and doc.translation_segments:
        translation = "\n\n".join(s.easy_text for s in doc.translation_segments if s.easy_text)
    if translation:
        parts.append(f"이지리드 번역:\n{translation[:4000]}")
    if doc.full_text and not doc.summary:
        parts.append(f"원문 발췌:\n{doc.full_text[:2000]}")
    return "\n\n".join(parts)


def _format_db_context(hits: list[dict[str, str]]) -> str:
    if not hits:
        return "(DB에서 관련 항목을 찾지 못했습니다.)"
    blocks: list[str] = []
    for i, hit in enumerate(hits, 1):
        title = f" — {hit['title']}" if hit.get("title") else ""
        blocks.append(
            f"{i}. [원문] {hit['original']}{title}\n   [이지리드] {hit['easy_text']}"
        )
    return "\n\n".join(blocks)


def _build_user_payload(
    question: str,
    *,
    db_context: str,
    doc_context: str,
    web_context: str = "",
) -> str:
    sections = [
        "## 사용자 질문",
        question.strip(),
        "",
        "## DB 자료 (법률·이지리드 사례)",
        db_context,
    ]
    if doc_context.strip():
        sections.extend(["", "## 현재 작업 중인 문서", doc_context.strip()])
    if web_context.strip():
        sections.extend(["", "## 웹 검색 결과", web_context.strip()])
    sections.extend(
        [
            "",
            "## 지시",
            "위 자료를 우선 활용해 질문에 답하세요. 웹 검색 결과가 있으면 출처 불확실성을 밝히세요.",
        ]
    )
    return "\n".join(sections)


def _messages_for_solar(
    system: str,
    history: list[ChatMessage],
    user_payload: str,
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = [{"role": "system", "content": system}]
    for msg in history[-10:]:
        messages.append({"role": msg.role, "content": msg.content})
    messages.append({"role": "user", "content": user_payload})
    return messages


def _needs_web_fallback(reply: str, db_hits: list[dict], doc_context: str) -> bool:
    normalized = reply.strip().upper()
    if NEED_WEB_MARKER in normalized:
        return True
    if db_hits or doc_context.strip():
        return False
    return True


async def answer_chat(
    question: str,
    *,
    doc_id: str | None = None,
    history: list[ChatMessage] | None = None,
) -> ChatResponse:
    history = history or []
    system = load_chatbot_prompt()
    db_hits = search_legal_db(question)
    db_context = _format_db_context(db_hits)
    doc_context = await build_document_context(doc_id)
    sources: list[str] = []

    payload = _build_user_payload(
        question,
        db_context=db_context,
        doc_context=doc_context,
    )
    messages = _messages_for_solar(system, history, payload)
    reply = await upstage.chat_completion_messages(messages, max_tokens=2048)

    if _needs_web_fallback(reply, db_hits, doc_context):
        web_context = await search_web(question)
        if web_context.strip():
            sources.append("web")
            payload = _build_user_payload(
                question,
                db_context=db_context,
                doc_context=doc_context,
                web_context=web_context,
            )
            messages = _messages_for_solar(system, history, payload)
            reply = await upstage.chat_completion_messages(messages, max_tokens=2048)
        elif not db_hits and not doc_context.strip():
            reply = (
                "죄송합니다. DB와 웹에서도 관련 정보를 찾지 못했습니다. "
                "질문을 조금 다르게 바꿔 주시거나, 작업 중인 문서가 있다면 해당 화면에서 다시 물어봐 주세요."
            )
    else:
        reply = reply.replace(NEED_WEB_MARKER, "").strip()
        if db_hits:
            sources.append("db")
        if doc_context.strip():
            sources.append("document")

    if not sources:
        sources.append("solar")

    return ChatResponse(reply=reply.strip(), sources=sources)
