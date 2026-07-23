from __future__ import annotations

"""문서 API 라우터.

역할: /api/documents 하위 REST 엔드포인트를 제공한다.
주요 기능: 업로드(OCR), 조회, 요약·번역·체크리스트, 이미지 배치, Word 내보내기.
관계: database(저장), services(upstage·translator·word_export 등), models/schemas(요청·응답).
"""

import mimetypes
import re
import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, File, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response

from backend.config import UPLOAD_DIR, settings
from backend.database import (
    create_document,
    ensure_document,
    get_document,
    get_doc_type,
    get_page,
    update_doc_type,
    update_summary,
    update_translation,
)
from backend.models.schemas import (
    ChecklistReport,
    DocTypeUpdate,
    DocumentResponse,
    DocumentEnsureRequest,
    DetectPlacementsRequest,
    ExportRequest,
    ImageCatalogItem,
    ImagePlacement,
    ArtifactTextResponse,
    RefineRequest,
    SummarizeRequest,
    SummaryUpdate,
    TranslationUpdate,
    UserProjectItem,
    UploadResponse,
)
from backend.services.image_matcher import (
    detect_image_placements,
    fill_missing_item_placements_async,
    list_image_catalog,
)
from backend.services.image_assets import resolve_image_path, resolve_placement_image
from backend.services.image_web_search import search_web_images
from backend.services.easy_read_sanitize import extract_refined_translation
from backend.services import parser, prompts, translator, upstage, word_export, pdf_export, user_storage, merged_export

router = APIRouter(prefix="/documents", tags=["documents"])

# --- 헬퍼: 원본 파일 경로 ---


def _source_path(doc_id: str, filename: str) -> Path:
    ext = Path(filename).suffix.lower() or ".bin"
    return UPLOAD_DIR / f"{doc_id}_source{ext}"

# --- 요약 텍스트 정규화·교정 ---


_ITEM_START_RE = re.compile(
    r"^(?:<[^>]{1,60}>|\(\d+\)|\d+[.)]|[①②③④⑤⑥⑦⑧⑨⑩]|[가-힣]\.|[-*•·])\s*"
)

_PAGE_NUMBER_RE = re.compile(r"^\s*-\s*\d+\s*-\s*$")

_SUMMARY_HEADING_ALIASES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^(?:1[.)]\s*)?<*\s*먼저,?\s*법원의\s*판단을\s*알려드립니다\s*>*$"), "<먼저, 법원의 판단을 알려드립니다>"),
    (re.compile(r"^(?:2[.)]\s*)?<*\s*어떤\s*일이\s*있었나요\??\s*>*$"), "<어떤 일이 있었나요?>"),
    (re.compile(r"^(?:3[.)]\s*)?<*\s*법원이\s*판단한\s*문제는\s*무엇인가요\??\s*>*$"), "<법원이 판단한 문제는 무엇인가요?>"),
    (re.compile(r"^(?:4[.)]\s*)?<*\s*법원은\s*어떻게\s*판단했나요\??\s*>*$"), "<법원은 어떻게 판단했나요?>"),
    (re.compile(r"^(?:5[.)]\s*)?<*\s*최종\s*결과는\s*무엇인가요\??\s*>*$"), "<최종 결과는 무엇인가요?>"),
]

_TRUNCATED_END_RE = re.compile(r"[,:;\-\(\[\{]$|(?:및|또는|등)$")


def _canonicalize_summary_heading(line: str) -> str | None:
    target = line.strip()
    for pattern, canonical in _SUMMARY_HEADING_ALIASES:
        if pattern.match(target):
            return canonical
    return None


def _standardize_summary_sections(text: str) -> str:
    lines = text.split("\n")
    out: list[str] = []
    for raw in lines:
        stripped = raw.strip()
        canonical = _canonicalize_summary_heading(stripped)
        if canonical:
            if out and out[-1] != "":
                out.append("")
            out.append(canonical)
            continue
        out.append(raw.rstrip())
    return "\n".join(out)


def _enforce_item_paragraphs(text: str) -> str:
    lines = text.split("\n")
    out: list[str] = []
    prev_nonblank = ""

    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()

        if not stripped:
            if out and out[-1] != "":
                out.append("")
            continue

        if _PAGE_NUMBER_RE.match(stripped):
            continue

        is_item_start = bool(_ITEM_START_RE.match(stripped))
        if out and out[-1] != "" and is_item_start and prev_nonblank:
            out.append("")

        out.append(stripped)
        prev_nonblank = stripped

    return "\n".join(out).strip()


def _normalize_summary_text(text: str) -> str:
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = _standardize_summary_sections(cleaned)
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = _enforce_item_paragraphs(cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _count_canonical_headings(text: str) -> int:
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    count = 0
    for line in lines:
        if _canonicalize_summary_heading(line):
            count += 1
    return count


def _looks_truncated_summary(base: str, candidate: str) -> bool:
    if not candidate:
        return True

    if len(candidate) < max(240, int(len(base) * 0.65)):
        return True

    base_headings = _count_canonical_headings(base)
    cand_headings = _count_canonical_headings(candidate)
    if base_headings >= 3 and cand_headings < max(1, base_headings - 1):
        return True

    tail = candidate.strip()
    if _TRUNCATED_END_RE.search(tail):
        return True

    return False


async def _polish_summary_text(text: str) -> str:
    base = _normalize_summary_text(text)
    if not base:
        return base
    if settings.use_mock:
        return base

    system = (
        "당신은 한국어 교정 편집자입니다. 문서의 의미·사실관계는 절대 바꾸지 말고, "
        "맞춤법/띄어쓰기/문장부호를 교정하고 문단 구분만 읽기 쉽게 정리하세요."
    )
    user = (
        "아래 요약문을 교정하세요.\n"
        "- 원래 의미·결론·수치·고유명사는 변경 금지\n"
        "- 맞춤법, 띄어쓰기, 문장부호만 교정\n"
        "- 문단은 주제 전환마다 줄바꿈하여 읽기 쉽게 정리\n"
        "- 제목, 주석, 설명 문구를 추가하지 말고 교정된 본문만 출력\n\n"
        f"{base}"
    )
    try:
        polished = await upstage.chat_completion(system, user, max_tokens=5000)
    except Exception:
        return base
    polished = _normalize_summary_text(polished)
    if _looks_truncated_summary(base, polished):
        return base
    return polished or base

# --- Word 내보내기 헬퍼 ---


def _doc_for_export(doc_id: str, doc: DocumentResponse | None, body: ExportRequest | None) -> DocumentResponse | None:
    if body and body.segments:
        text = body.translation_text or "\n\n".join(
            s.easy_text for s in body.segments if s.easy_text
        )
        if doc:
            return doc.model_copy(
                update={
                    "summary": body.summary if body.summary is not None else doc.summary,
                    "translation_segments": body.segments,
                    "translation_text": text,
                    "stage": "translated",
                }
            )
        return DocumentResponse(
            id=doc_id,
            filename=body.filename or "document.pdf",
            doc_type=body.doc_type or "unknown",
            stage="translated",
            page_count=len(body.pages or []),
            full_text=body.full_text or "",
            summary=body.summary,
            translation_segments=body.segments,
            translation_text=text,
        )
    return doc


async def _resolve_document(doc_id: str, body: ExportRequest | None) -> DocumentResponse | None:
    doc = await get_document(doc_id)
    if doc:
        return doc
    if body and body.full_text:
        pages = body.pages or [body.full_text]
        await ensure_document(
            doc_id,
            filename=body.filename or "upload.pdf",
            doc_type=body.doc_type or "unknown",
            pages=pages,
            full_text=body.full_text,
        )
        return await get_document(doc_id)
    return None


async def _resolve_parsed_pages_for_insert(
    doc_id: str,
    doc: DocumentResponse,
) -> list[str]:
    pages: list[str] = []
    for page_num in range(1, max(0, doc.page_count) + 1):
        page = await get_page(doc_id, page_num)
        if page is not None:
            pages.append(page)

    if pages:
        return pages

    return [doc.full_text] if doc.full_text else []


async def _ensure_doc_from_request(
    doc_id: str,
    body: DocumentEnsureRequest | None,
) -> DocumentResponse:
    doc = await get_document(doc_id)
    if doc:
        return doc
    if not body or not body.full_text:
        raise HTTPException(404, "문서를 찾을 수 없습니다.")
    pages = body.pages or [body.full_text]
    await ensure_document(
        doc_id,
        filename=body.filename or "upload.pdf",
        doc_type=body.doc_type or "unknown",
        pages=pages,
        full_text=body.full_text,
    )
    if body.summary is not None and body.summary.strip():
        await update_summary(doc_id, body.summary)
    doc = await get_document(doc_id)
    if not doc:
        raise HTTPException(404, "문서를 복구하지 못했습니다.")
    return doc


def _require_user_id(x_user_id: str | None, user_id_query: str | None = None) -> str:
    user_id = (x_user_id or user_id_query or "").strip()
    if not user_id:
        raise HTTPException(401, "로그인 사용자가 필요합니다.")
    return user_id


def _validate_export_images(doc: DocumentResponse) -> None:
    """Fail fast when export placements reference missing images.

    Without this check, export can silently degrade to text-only output.
    """
    missing: list[str] = []
    for segment in doc.translation_segments or []:
        for placement in segment.image_placements or []:
            if placement.image_base64 and placement.image_base64.strip():
                continue
            resolved = resolve_placement_image(
                image_file=placement.image_file,
                image_url=placement.image_url,
            )
            if not resolved:
                missing.append(placement.image_file)

    if missing:
        sample = ", ".join(sorted(set(missing))[:6])
        raise HTTPException(
            400,
            f"그림 파일을 찾지 못했습니다: {sample}. 그림 페이지에서 다시 배치 후 추출해 주세요.",
        )

# --- 업로드·조회 ---


@router.get("/catalog/images", response_model=list[ImageCatalogItem])
async def image_catalog(q: str = Query("")) -> list[ImageCatalogItem]:
    return [ImageCatalogItem(**item) for item in list_image_catalog(q)]


@router.get("/assets/images/{image_file}")
async def read_image_asset(image_file: str) -> FileResponse:
    # Block path traversal and normalize to file name only.
    safe_name = Path(image_file).name
    resolved = resolve_image_path(safe_name)
    if not resolved:
        raise HTTPException(404, "그림 파일을 찾을 수 없습니다.")
    media_type, _ = mimetypes.guess_type(safe_name)
    return FileResponse(
        path=resolved,
        media_type=media_type or "application/octet-stream",
        filename=safe_name,
        content_disposition_type="inline",
    )


@router.get("/catalog/images/web", response_model=list[ImageCatalogItem])
async def image_catalog_web(q: str = Query(..., min_length=1)) -> list[ImageCatalogItem]:
    """AI 프롬프트 기반 웹 이미지 검색 (그림 탭)."""
    items = await search_web_images(q)
    return [ImageCatalogItem(**item) for item in items]


@router.post("/upload", response_model=UploadResponse)
async def upload_document(
    file: UploadFile = File(...),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
) -> UploadResponse:
    if not file.filename:
        raise HTTPException(400, "파일명이 없습니다.")

    ext = Path(file.filename).suffix.lower()
    allowed = {".pdf", ".png", ".jpg", ".jpeg", ".txt", ".doc", ".docx", ".hwp", ".hwpx"}
    if ext not in allowed:
        raise HTTPException(400, f"지원하지 않는 형식: {ext}")

    content = await file.read()
    save_path = UPLOAD_DIR / f"{uuid.uuid4()}{ext}"
    save_path.write_bytes(content)

    try:
        pages, full_text = await upstage.extract_text_from_file(save_path, file.filename)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    doc_type = parser.classify_doc_type(full_text)

    doc_id = await create_document(file.filename, doc_type, pages, full_text)
    _source_path(doc_id, file.filename).write_bytes(content)
    if x_user_id:
        user_storage.save_source(x_user_id, doc_id, file.filename, content)
    return UploadResponse(
        id=doc_id,
        filename=file.filename,
        doc_type=doc_type,
        page_count=len(pages),
        message="업로드 및 OCR 완료",
        pages=pages,
        full_text=full_text,
    )


@router.get("/user-projects", response_model=list[UserProjectItem])
async def list_user_projects(
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
    user_id: str | None = Query(default=None),
) -> list[UserProjectItem]:
    resolved = _require_user_id(x_user_id, user_id)
    return [UserProjectItem(**item) for item in user_storage.list_user_projects(resolved)]


@router.get("/user-projects/{doc_id}/artifact/{kind}", response_model=ArtifactTextResponse)
async def open_user_project_artifact(
    doc_id: str,
    kind: Literal["summary", "translation", "easyread"],
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
    user_id: str | None = Query(default=None),
) -> ArtifactTextResponse:
    resolved = _require_user_id(x_user_id, user_id)
    text = user_storage.read_artifact_text(resolved, doc_id, kind)
    if text is None:
        raise HTTPException(404, "저장된 파일을 찾을 수 없습니다.")
    return ArtifactTextResponse(content=text)


@router.get("/user-projects/{doc_id}/source")
async def open_user_project_source(
    doc_id: str,
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
    user_id: str | None = Query(default=None),
) -> FileResponse:
    resolved_user = _require_user_id(x_user_id, user_id)
    resolved = user_storage.get_source_file(resolved_user, doc_id)
    if not resolved:
        raise HTTPException(404, "원본 파일을 찾을 수 없습니다.")
    path, filename = resolved
    media_type, _ = mimetypes.guess_type(filename)
    return FileResponse(
        path=path,
        media_type=media_type or "application/octet-stream",
        filename=filename,
        content_disposition_type="inline",
    )


@router.get("/user-projects/{doc_id}/easyread.pdf")
async def open_user_project_easyread_pdf(
    doc_id: str,
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
    user_id: str | None = Query(default=None),
) -> FileResponse:
    resolved_user = _require_user_id(x_user_id, user_id)
    resolved = user_storage.get_easyread_pdf_file(resolved_user, doc_id)
    if not resolved:
        raise HTTPException(404, "저장된 이지리드 PDF를 찾을 수 없습니다.")
    path, filename = resolved
    return FileResponse(
        path=path,
        media_type="application/pdf",
        filename=filename,
        content_disposition_type="inline",
    )


@router.delete("/user-projects/{doc_id}", status_code=204)
async def delete_user_project(
    doc_id: str,
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
    user_id: str | None = Query(default=None),
) -> Response:
    resolved_user = _require_user_id(x_user_id, user_id)
    deleted = user_storage.delete_user_project(resolved_user, doc_id)
    if not deleted:
        raise HTTPException(404, "삭제할 프로젝트를 찾을 수 없습니다.")
    return Response(status_code=204)


@router.get("/{doc_id}/source")
async def read_source_file(
    doc_id: str,
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
    user_id: str | None = Query(default=None),
) -> FileResponse:
    doc = await get_document(doc_id)
    if not doc:
        raise HTTPException(404, "문서를 찾을 수 없습니다.")

    path = _source_path(doc_id, doc.filename)
    if not path.is_file():
        resolved_user = (x_user_id or user_id or "").strip()
        if resolved_user:
            stored = user_storage.get_source_file(resolved_user, doc_id)
            if stored:
                path, filename = stored
                media_type, _ = mimetypes.guess_type(filename)
                return FileResponse(
                    path=path,
                    media_type=media_type or "application/octet-stream",
                    filename=filename,
                    content_disposition_type="inline",
                )
        raise HTTPException(404, "원본 파일을 찾을 수 없습니다.")

    media_type, _ = mimetypes.guess_type(doc.filename)
    return FileResponse(
        path=path,
        media_type=media_type or "application/octet-stream",
        filename=doc.filename,
        content_disposition_type="inline",
    )


@router.get("/{doc_id}/source.pdf")
async def read_source_pdf(
    doc_id: str,
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
    user_id: str | None = Query(default=None),
) -> Response:
    doc = await get_document(doc_id)
    if not doc:
        raise HTTPException(404, "문서를 찾을 수 없습니다.")

    ext = Path(doc.filename).suffix.lower()
    if ext not in {".doc", ".docx"}:
        raise HTTPException(400, "Word 파일만 PDF로 미리볼 수 있습니다.")

    path = _source_path(doc_id, doc.filename)
    source_bytes: bytes | None = None
    source_name = doc.filename
    if path.is_file():
        source_bytes = path.read_bytes()
    else:
        resolved_user = (x_user_id or user_id or "").strip()
        if resolved_user:
            stored = user_storage.get_source_file(resolved_user, doc_id)
            if stored:
                source_path, source_name = stored
                source_bytes = source_path.read_bytes()
    if source_bytes is None:
        raise HTTPException(404, "원본 파일을 찾을 수 없습니다.")

    from backend.services.docx_to_pdf import DocxToPdfError, convert_file_bytes_to_pdf

    try:
        content = convert_file_bytes_to_pdf(source_bytes, source_name)
    except DocxToPdfError as exc:
        raise HTTPException(
            503,
            "서버에 Word PDF 변환기가 없습니다. Word 미리보기를 사용할 수 없습니다.",
        ) from exc

    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{Path(doc.filename).stem}.pdf"'},
    )


@router.head("/{doc_id}/source.pdf")
async def head_source_pdf(
    doc_id: str,
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
    user_id: str | None = Query(default=None),
) -> Response:
    doc = await get_document(doc_id)
    if not doc:
        raise HTTPException(404, "문서를 찾을 수 없습니다.")

    ext = Path(doc.filename).suffix.lower()
    if ext not in {".doc", ".docx"}:
        raise HTTPException(400, "Word 파일만 PDF로 미리볼 수 있습니다.")

    path = _source_path(doc_id, doc.filename)
    if not path.is_file():
        resolved_user = (x_user_id or user_id or "").strip()
        if not resolved_user or not user_storage.get_source_file(resolved_user, doc_id):
            raise HTTPException(404, "원본 파일을 찾을 수 없습니다.")

    from backend.services.docx_to_pdf import conversion_backend_hint

    if conversion_backend_hint() in {"unavailable", "convertapi-required"}:
      raise HTTPException(
          503,
          "서버에 Word PDF 변환기가 없습니다. Word 미리보기를 사용할 수 없습니다.",
      )

    return Response(status_code=200, media_type="application/pdf")


@router.head("/{doc_id}/source")
async def head_source_file(
    doc_id: str,
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
    user_id: str | None = Query(default=None),
) -> Response:
    doc = await get_document(doc_id)
    if not doc:
        raise HTTPException(404, "문서를 찾을 수 없습니다.")

    path = _source_path(doc_id, doc.filename)
    if not path.is_file():
        resolved_user = (x_user_id or user_id or "").strip()
        if not resolved_user or not user_storage.get_source_file(resolved_user, doc_id):
            raise HTTPException(404, "원본 파일을 찾을 수 없습니다.")

    media_type, _ = mimetypes.guess_type(doc.filename)
    return Response(status_code=200, media_type=media_type or "application/octet-stream")


@router.get("/{doc_id}", response_model=DocumentResponse)
async def read_document(doc_id: str) -> DocumentResponse:
    doc = await get_document(doc_id)
    if not doc:
        raise HTTPException(404, "문서를 찾을 수 없습니다.")
    return doc


@router.patch("/{doc_id}/doc-type", response_model=DocumentResponse)
async def patch_doc_type(doc_id: str, body: DocTypeUpdate) -> DocumentResponse:
    doc = await get_document(doc_id)
    if not doc:
        raise HTTPException(404, "문서를 찾을 수 없습니다.")
    if body.doc_type == "unknown":
        raise HTTPException(400, "unknown 유형은 선택할 수 없습니다.")
    await update_doc_type(doc_id, body.doc_type)
    updated = await get_document(doc_id)
    if not updated:
        raise HTTPException(404, "문서를 갱신하지 못했습니다.")
    return updated


@router.get("/{doc_id}/pages/{page_num}")
async def read_page(doc_id: str, page_num: int) -> dict[str, str]:
    page = await get_page(doc_id, page_num)
    if page is None:
        raise HTTPException(404, "페이지를 찾을 수 없습니다.")
    return {"page": page}

# --- 요약 API ---


@router.post("/{doc_id}/summarize", response_model=DocumentResponse)
async def summarize_document(
    doc_id: str,
    force: bool = Query(False),
    body: SummarizeRequest | None = None,
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
) -> DocumentResponse:
    doc = await get_document(doc_id)
    if not doc:
        if not body or not body.full_text:
            raise HTTPException(404, "문서를 찾을 수 없습니다.")
        pages = body.pages or [body.full_text]
        await ensure_document(
            doc_id,
            filename=body.filename or "upload.pdf",
            doc_type=body.doc_type or "unknown",
            pages=pages,
            full_text=body.full_text,
        )
        doc = await get_document(doc_id)
        if not doc:
            raise HTTPException(404, "문서를 복구하지 못했습니다.")
    if doc.summary and not force:
        return doc

    system = prompts.build_summary_system_prompt(doc.doc_type)
    user = f"다음 판결문을 요약하세요:\n\n{doc.full_text}"
    summary = await upstage.chat_completion(system, user)
    summary = await _polish_summary_text(summary)
    await update_summary(doc_id, summary)
    updated = await get_document(doc_id)
    if updated and x_user_id and updated.summary:
        user_storage.save_summary(x_user_id, doc_id, updated.filename, updated.summary)
    return updated  # type: ignore[return-value]


@router.patch("/{doc_id}/summary", response_model=DocumentResponse)
async def patch_summary(
    doc_id: str,
    body: SummaryUpdate,
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
) -> DocumentResponse:
    doc = await get_document(doc_id)
    if not doc and body.full_text:
        pages = body.pages or [body.full_text]
        await ensure_document(
            doc_id,
            filename=body.filename or "upload.pdf",
            doc_type=body.doc_type or "unknown",
            pages=pages,
            full_text=body.full_text,
        )
        doc = await get_document(doc_id)
    if not doc:
        raise HTTPException(404, "문서를 찾을 수 없습니다.")
    await update_summary(doc_id, body.summary)
    updated = await get_document(doc_id)
    if updated and x_user_id and updated.summary:
        user_storage.save_summary(x_user_id, doc_id, updated.filename, updated.summary)
    return updated  # type: ignore[return-value]


@router.post("/{doc_id}/summary/refine", response_model=DocumentResponse)
async def refine_summary(
    doc_id: str,
    body: RefineRequest,
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
) -> DocumentResponse:
    doc = await get_document(doc_id)
    if not doc:
        raise HTTPException(404, "문서를 찾을 수 없습니다.")
    current = (body.summary if body.summary is not None else doc.summary) or ""
    if body.summary is not None and body.summary.strip():
        await update_summary(doc_id, body.summary.strip())
    system = prompts.build_summary_system_prompt(doc.doc_type)
    user = (
        f"현재 요약:\n{current}\n\n"
        f"다음 지시에 따라 요약을 **수정**하세요:\n{body.prompt}\n\n"
        "규칙:\n"
        "- 사용자 지시를 **반드시 반영**하세요.\n"
        "- **수정된 요약 본문만** 출력하세요. 설명·메타 제목은 출력하지 마세요."
    )
    revised = await upstage.chat_completion(system, user, max_tokens=5000, temperature=0.45)
    revised = extract_refined_translation(revised, fallback=current)
    revised = await _polish_summary_text(revised)
    await update_summary(doc_id, revised)
    updated = await get_document(doc_id)
    if updated and x_user_id and updated.summary:
        user_storage.save_summary(x_user_id, doc_id, updated.filename, updated.summary)
    return updated  # type: ignore[return-value]

# --- 쉬운 글(번역) API ---


@router.post("/{doc_id}/translate", response_model=DocumentResponse)
async def translate_document(
    doc_id: str,
    body: DocumentEnsureRequest | None = None,
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
) -> DocumentResponse:
    doc = await _ensure_doc_from_request(doc_id, body)
    if not doc.summary:
        raise HTTPException(400, "먼저 요약을 생성하세요.")
    if doc.translation_segments:
        return doc

    segments, text, checklist_report = await translator.translate_summary(
        doc.summary, doc.full_text, doc.doc_type
    )
    await update_translation(doc_id, segments, text, checklist=checklist_report)
    updated = await get_document(doc_id)
    if updated and x_user_id and updated.translation_text:
        user_storage.save_translation(x_user_id, doc_id, updated.filename, updated.translation_text)
        user_storage.save_easyread_text(x_user_id, doc_id, updated.filename, updated.translation_text)
    return updated  # type: ignore[return-value]


@router.post("/{doc_id}/translation/detect-placements", response_model=list[ImagePlacement])
async def detect_translation_placements(
    doc_id: str,
    body: DetectPlacementsRequest | None = None,
) -> list[ImagePlacement]:
    doc = await _ensure_doc_from_request(doc_id, body)
    text = (
        (body.translation_text if body and body.translation_text else None)
        or doc.translation_text
        or "\n\n".join(s.easy_text for s in doc.translation_segments if s.easy_text)
    )
    if not text.strip():
        raise HTTPException(400, "번역본이 없습니다.")
    existing = body.existing_placements if body else []
    return await fill_missing_item_placements_async(text, existing)


@router.patch("/{doc_id}/translation", response_model=DocumentResponse)
async def patch_translation(
    doc_id: str,
    body: TranslationUpdate,
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
) -> DocumentResponse:
    doc = await get_document(doc_id)
    if not doc and body.full_text:
        pages = body.pages or [body.full_text]
        await ensure_document(
            doc_id,
            filename=body.filename or "upload.pdf",
            doc_type=body.doc_type or "unknown",
            pages=pages,
            full_text=body.full_text,
        )
        doc = await get_document(doc_id)
    if not doc:
        raise HTTPException(404, "문서를 찾을 수 없습니다.")
    if body.summary is not None and body.summary.strip():
        await update_summary(doc_id, body.summary)
        doc = await get_document(doc_id)
    if not doc:
        raise HTTPException(404, "문서를 찾을 수 없습니다.")
    text = "\n\n".join(s.easy_text for s in body.segments)
    checklist_report = translator.run_checklist(text)
    await update_translation(doc_id, body.segments, text, checklist=checklist_report)
    updated = await get_document(doc_id)
    if updated and x_user_id and updated.translation_text:
        user_storage.save_translation(x_user_id, doc_id, updated.filename, updated.translation_text)
        user_storage.save_easyread_text(x_user_id, doc_id, updated.filename, updated.translation_text)
    return updated  # type: ignore[return-value]


@router.post("/{doc_id}/translation/refine", response_model=DocumentResponse)
async def refine_translation(
    doc_id: str,
    body: RefineRequest,
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
) -> DocumentResponse:
    doc = await get_document(doc_id)
    if not doc:
        raise HTTPException(404, "문서를 찾을 수 없습니다.")
    doc_type = await get_doc_type(doc_id) or "unknown"
    segments = body.segments if body.segments is not None else doc.translation_segments
    if body.segments is not None and body.segments:
        text = "\n\n".join(s.easy_text for s in body.segments if s.easy_text)
        await update_translation(doc_id, body.segments, text)
    try:
        segments, text, checklist_report = await translator.refine_translation(
            segments, body.prompt, doc_type
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    await update_translation(doc_id, segments, text, checklist=checklist_report)
    updated = await get_document(doc_id)
    if updated and x_user_id and updated.translation_text:
        user_storage.save_translation(x_user_id, doc_id, updated.filename, updated.translation_text)
        user_storage.save_easyread_text(x_user_id, doc_id, updated.filename, updated.translation_text)
    return updated  # type: ignore[return-value]


@router.post("/{doc_id}/translation/checklist", response_model=ChecklistReport)
async def run_translation_checklist(doc_id: str) -> ChecklistReport:
    doc = await get_document(doc_id)
    if not doc:
        raise HTTPException(404, "문서를 찾을 수 없습니다.")
    text = doc.translation_text or "\n\n".join(
        s.easy_text for s in doc.translation_segments
    )
    if not text.strip():
        raise HTTPException(400, "번역본이 없습니다.")
    report = translator.run_checklist(text)
    if doc.translation_segments:
        await update_translation(
            doc_id, doc.translation_segments, text, checklist=report
        )
    return report

# --- Word 내보내기 ---


@router.get("/{doc_id}/export.docx")
async def export_docx_get(
    doc_id: str,
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
) -> Response:
    return await _export_docx(doc_id, None, x_user_id=x_user_id)


@router.post("/{doc_id}/export.docx")
async def export_docx_post(
    doc_id: str,
    body: ExportRequest | None = None,
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
) -> Response:
    return await _export_docx(doc_id, body, x_user_id=x_user_id)


async def _export_docx(
    doc_id: str,
    body: ExportRequest | None,
    *,
    x_user_id: str | None,
) -> Response:
    doc = await _resolve_document(doc_id, body)
    doc = _doc_for_export(doc_id, doc, body)
    if not doc:
        raise HTTPException(404, "문서를 찾을 수 없습니다.")
    if not doc.translation_text and not doc.summary and not doc.translation_segments:
        raise HTTPException(400, "내보낼 내용이 없습니다.")
    _validate_export_images(doc)

    content = word_export.export_to_docx(doc)
    if x_user_id:
        easyread_text = doc.translation_text or doc.summary or ""
        if easyread_text:
            user_storage.save_easyread_text(x_user_id, doc_id, doc.filename, easyread_text)
        user_storage.save_easyread_docx(x_user_id, doc_id, doc.filename, content)
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="easyread_{doc_id[:8]}.docx"'},
    )


# --- PDF 내보내기 ---


@router.get("/{doc_id}/export.pdf")
async def export_pdf_get(
    doc_id: str,
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
) -> Response:
    return await _export_pdf(doc_id, None, inline=True, x_user_id=x_user_id)


@router.post("/{doc_id}/export.pdf")
async def export_pdf_post(
    doc_id: str,
    body: ExportRequest | None = None,
    download: bool = Query(False),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
) -> Response:
    return await _export_pdf(doc_id, body, inline=not download, x_user_id=x_user_id)


async def _export_pdf(
    doc_id: str,
    body: ExportRequest | None,
    *,
    inline: bool,
    x_user_id: str | None,
) -> Response:
    doc = await _resolve_document(doc_id, body)
    doc = _doc_for_export(doc_id, doc, body)
    if not doc:
        raise HTTPException(404, "문서를 찾을 수 없습니다.")
    if not doc.translation_text and not doc.summary and not doc.translation_segments:
        raise HTTPException(400, "내보낼 내용이 없습니다.")
    _validate_export_images(doc)

    from backend.services.docx_to_pdf import DocxToPdfError

    try:
        content = pdf_export.export_to_pdf(doc)
    except DocxToPdfError as exc:
        try:
            # Converter-free fallback: render Easy-Read directly via PyMuPDF Story.
            content = pdf_export.export_to_pdf_without_docx_converter(doc)
        except Exception as fallback_exc:  # noqa: BLE001
            raise HTTPException(
                503,
                "서버에 Word/LibreOffice PDF 변환기가 없고, 대체 PDF 생성도 실패했습니다.",
            ) from fallback_exc

    if x_user_id:
        easyread_text = doc.translation_text or doc.summary or ""
        if easyread_text:
            user_storage.save_easyread_text(x_user_id, doc_id, doc.filename, easyread_text)
        user_storage.save_easyread_pdf(x_user_id, doc_id, doc.filename, content)

    disposition = "inline" if inline else "attachment"
    return Response(
        content=content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'{disposition}; filename="easyread_{doc_id[:8]}.pdf"'
        },
    )


@router.post("/{doc_id}/export.merged.pdf")
async def export_merged_pdf_post(
    doc_id: str,
    body: ExportRequest | None = None,
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
) -> Response:
    doc = await _resolve_document(doc_id, body)
    doc = _doc_for_export(doc_id, doc, body)
    if not doc:
        raise HTTPException(404, "문서를 찾을 수 없습니다.")
    if not doc.translation_text and not doc.summary and not doc.translation_segments:
        raise HTTPException(400, "내보낼 내용이 없습니다.")
    _validate_export_images(doc)

    source_path = _source_path(doc_id, doc.filename)
    if source_path.is_file():
        source_bytes = source_path.read_bytes()
    else:
        if not x_user_id:
            raise HTTPException(404, "원본 파일을 찾을 수 없습니다.")
        stored = user_storage.get_source_file(x_user_id, doc_id)
        if not stored:
            raise HTTPException(404, "원본 파일을 찾을 수 없습니다.")
        source_path, _stored_name = stored
        source_bytes = source_path.read_bytes()
    easyread_docx = word_export.export_to_docx(doc)
    original_pages = await _resolve_parsed_pages_for_insert(doc_id, doc)
    merged_pdf = b""

    try:
        merged_docx = merged_export.export_merged_docx(
            original_source_bytes=source_bytes,
            original_filename=doc.filename,
            easyread_docx_bytes=easyread_docx,
            parsed_pages=original_pages,
            parsed_full_text=doc.full_text,
        )
        from backend.services.docx_to_pdf import DocxToPdfError, convert_file_bytes_to_pdf

        merged_pdf = convert_file_bytes_to_pdf(merged_docx, "merged.docx")
    except ValueError as exc:
        raise HTTPException(
            502,
            str(exc),
        ) from exc
    except DocxToPdfError as exc:
        try:
            merged_pdf = pdf_export.export_docx_bytes_to_pdf_without_converter(merged_docx)
        except Exception as fallback_exc:  # noqa: BLE001
            raise HTTPException(
                503,
                "서버에 Word/LibreOffice PDF 변환기가 없고, 합본 Word 기반 PDF 변환도 실패했습니다.",
            ) from fallback_exc

    if x_user_id:
        easyread_text = doc.translation_text or doc.summary or ""
        if easyread_text:
            user_storage.save_easyread_text(x_user_id, doc_id, doc.filename, easyread_text)
        user_storage.save_easyread_pdf(x_user_id, doc_id, doc.filename, merged_pdf)

    return Response(
        content=merged_pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="easyread_merged_{doc_id[:8]}.pdf"'
        },
    )
