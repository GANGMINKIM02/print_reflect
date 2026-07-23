from __future__ import annotations

"""회원별 프로젝트 결과 저장소.

역할: 회원 ID 기준으로 업로드 원본·요약·번역·이지리드 산출물을 파일로 저장한다.
주요 기능: 저장(save_*), 조회(list/read/get_source_file).
"""

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
import mimetypes
from urllib.parse import urlparse

from backend.config import DATA_DIR
from backend.services import blob_storage

_USER_STORAGE_DIR = DATA_DIR / "user_storage"


ArtifactKind = Literal["summary", "translation", "easyread"]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_segment(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", value.strip())
    return cleaned or "unknown"


def _user_dir(user_id: str) -> Path:
    return _USER_STORAGE_DIR / _safe_segment(user_id)


def _project_dir(user_id: str, doc_id: str) -> Path:
    return _user_dir(user_id) / "projects" / _safe_segment(doc_id)


def _meta_path(user_id: str, doc_id: str) -> Path:
    return _project_dir(user_id, doc_id) / "metadata.json"


def _meta_blob_path(user_id: str, doc_id: str) -> str:
    return _blob_pathname(user_id, doc_id, "metadata.json")


def _load_meta(user_id: str, doc_id: str) -> dict:
    path = _meta_path(user_id, doc_id)
    if not path.is_file():
        content = blob_storage.fetch_bytes_by_pathname(_meta_blob_path(user_id, doc_id))
        if content:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            except OSError:
                pass
        else:
            return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_meta(user_id: str, doc_id: str, payload: dict) -> None:
    path = _meta_path(user_id, doc_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(content, encoding="utf-8")
    blob_storage.put_bytes(
        _meta_blob_path(user_id, doc_id),
        content.encode("utf-8"),
        content_type="application/json; charset=utf-8",
    )


def _touch_meta(user_id: str, doc_id: str, *, filename: str | None = None) -> dict:
    meta = _load_meta(user_id, doc_id)
    now = _now_iso()
    if not meta.get("created_at"):
        meta["created_at"] = now
    meta["updated_at"] = now
    meta["doc_id"] = doc_id
    if filename:
        meta["filename"] = filename
    _save_meta(user_id, doc_id, meta)
    return meta


def _blob_pathname(user_id: str, doc_id: str, file_name: str) -> str:
    return (
        f"user-storage/{_safe_segment(user_id)}/"
        f"projects/{_safe_segment(doc_id)}/{_safe_segment(file_name)}"
    )


def _persist_file(
    user_id: str,
    doc_id: str,
    *,
    filename: str,
    local_name: str,
    content: bytes,
    meta_file_key: str,
    meta_blob_key: str,
    content_type: str | None = None,
) -> None:
    project_dir = _project_dir(user_id, doc_id)
    project_dir.mkdir(parents=True, exist_ok=True)
    local_path = project_dir / local_name
    local_path.write_bytes(content)

    meta = _touch_meta(user_id, doc_id, filename=filename)
    meta[meta_file_key] = local_name

    blob_url = blob_storage.put_bytes(
        _blob_pathname(user_id, doc_id, local_name),
        content,
        content_type=content_type or (mimetypes.guess_type(local_name)[0] or "application/octet-stream"),
    )
    if blob_url:
        meta[meta_blob_key] = blob_url

    _save_meta(user_id, doc_id, meta)


def _ensure_local_file(
    user_id: str,
    doc_id: str,
    *,
    file_key: str,
    blob_key: str,
) -> Path | None:
    meta = _load_meta(user_id, doc_id)
    local_name = meta.get(file_key)
    if not isinstance(local_name, str) or not local_name:
        return None

    path = _project_dir(user_id, doc_id) / local_name
    if path.is_file():
        return path

    blob_url = meta.get(blob_key)
    if isinstance(blob_url, str) and blob_url.strip():
        if blob_storage.download_to_path(blob_url, path):
            return path
    return None


def save_source(user_id: str, doc_id: str, filename: str, content: bytes) -> None:
    ext = Path(filename).suffix.lower() or ".bin"
    source_name = f"source{ext}"
    _persist_file(
        user_id,
        doc_id,
        filename=filename,
        local_name=source_name,
        content=content,
        meta_file_key="source_file",
        meta_blob_key="source_blob_url",
        content_type=mimetypes.guess_type(filename)[0] or "application/octet-stream",
    )


def save_summary(user_id: str, doc_id: str, filename: str, summary: str) -> None:
    _persist_file(
        user_id,
        doc_id,
        filename=filename,
        local_name="summary.txt",
        content=summary.encode("utf-8"),
        meta_file_key="summary_file",
        meta_blob_key="summary_blob_url",
        content_type="text/plain; charset=utf-8",
    )


def save_translation(user_id: str, doc_id: str, filename: str, translation: str) -> None:
    _persist_file(
        user_id,
        doc_id,
        filename=filename,
        local_name="translation.txt",
        content=translation.encode("utf-8"),
        meta_file_key="translation_file",
        meta_blob_key="translation_blob_url",
        content_type="text/plain; charset=utf-8",
    )


def save_easyread_text(user_id: str, doc_id: str, filename: str, content: str) -> None:
    _persist_file(
        user_id,
        doc_id,
        filename=filename,
        local_name="easyread.txt",
        content=content.encode("utf-8"),
        meta_file_key="easyread_file",
        meta_blob_key="easyread_blob_url",
        content_type="text/plain; charset=utf-8",
    )


def save_easyread_docx(user_id: str, doc_id: str, filename: str, content: bytes) -> None:
    _persist_file(
        user_id,
        doc_id,
        filename=filename,
        local_name="easyread.docx",
        content=content,
        meta_file_key="easyread_docx_file",
        meta_blob_key="easyread_docx_blob_url",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


def save_easyread_pdf(user_id: str, doc_id: str, filename: str, content: bytes) -> None:
    _persist_file(
        user_id,
        doc_id,
        filename=filename,
        local_name="easyread.pdf",
        content=content,
        meta_file_key="easyread_pdf_file",
        meta_blob_key="easyread_pdf_blob_url",
        content_type="application/pdf",
    )


def list_user_projects(user_id: str) -> list[dict]:
    root = _user_dir(user_id) / "projects"

    items: list[dict] = []
    seen_doc_ids: set[str] = set()

    if root.is_dir():
        for project_dir in root.iterdir():
            if not project_dir.is_dir():
                continue
            meta_path = project_dir / "metadata.json"
            if not meta_path.is_file():
                continue
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue

            doc_id = str(meta.get("doc_id") or project_dir.name)
            seen_doc_ids.add(doc_id)
            items.append(_meta_to_project_item(meta, fallback_doc_id=project_dir.name))

    prefix = f"user-storage/{_safe_segment(user_id)}/projects/"
    for url in blob_storage.list_urls(prefix):
        if not url.endswith("/metadata.json"):
            continue
        try:
            parsed = urlparse(url)
            parts = parsed.path.strip("/").split("/")
            # .../user-storage/{user}/projects/{doc_id}/metadata.json
            doc_id = parts[-2]
        except Exception:  # noqa: BLE001
            continue
        if doc_id in seen_doc_ids:
            continue

        project_dir = _project_dir(user_id, doc_id)
        meta_path = project_dir / "metadata.json"
        if not meta_path.is_file():
            blob_storage.download_to_path(url, meta_path)
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        seen_doc_ids.add(doc_id)
        items.append(_meta_to_project_item(meta, fallback_doc_id=doc_id))

    items.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
    return items


def _meta_to_project_item(meta: dict, *, fallback_doc_id: str) -> dict:
    return {
        "doc_id": str(meta.get("doc_id") or fallback_doc_id),
        "filename": str(meta.get("filename") or "(이름 없음)"),
        "created_at": str(meta.get("created_at") or ""),
        "updated_at": str(meta.get("updated_at") or ""),
        "has_summary": bool(meta.get("summary_file") or meta.get("summary_blob_url")),
        "has_translation": bool(meta.get("translation_file") or meta.get("translation_blob_url")),
        "has_easyread_pdf": bool(meta.get("easyread_pdf_file") or meta.get("easyread_pdf_blob_url")),
        "has_easyread": bool(
            meta.get("easyread_file")
            or meta.get("easyread_blob_url")
            or meta.get("easyread_docx_file")
            or meta.get("easyread_docx_blob_url")
            or meta.get("easyread_pdf_file")
            or meta.get("easyread_pdf_blob_url")
        ),
    }


def read_artifact_text(user_id: str, doc_id: str, kind: ArtifactKind) -> str | None:
    meta = _load_meta(user_id, doc_id)
    file_key_map = {
        "summary": "summary_file",
        "translation": "translation_file",
        "easyread": "easyread_file",
    }
    key = file_key_map[kind]
    filename = meta.get(key)
    if not isinstance(filename, str) or not filename:
        return None

    blob_key_map = {
        "summary": "summary_blob_url",
        "translation": "translation_blob_url",
        "easyread": "easyread_blob_url",
    }
    path = _ensure_local_file(
        user_id,
        doc_id,
        file_key=key,
        blob_key=blob_key_map[kind],
    )
    if not path:
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def get_source_file(user_id: str, doc_id: str) -> tuple[Path, str] | None:
    meta = _load_meta(user_id, doc_id)
    source_file = meta.get("source_file")
    filename = meta.get("filename")
    if not isinstance(source_file, str) or not source_file:
        return None

    path = _ensure_local_file(
        user_id,
        doc_id,
        file_key="source_file",
        blob_key="source_blob_url",
    )
    if not path:
        return None

    download_name = str(filename) if isinstance(filename, str) and filename else path.name
    return path, download_name


def get_easyread_pdf_file(user_id: str, doc_id: str) -> tuple[Path, str] | None:
    meta = _load_meta(user_id, doc_id)
    pdf_file = meta.get("easyread_pdf_file")
    if not isinstance(pdf_file, str) or not pdf_file:
        return None

    path = _ensure_local_file(
        user_id,
        doc_id,
        file_key="easyread_pdf_file",
        blob_key="easyread_pdf_blob_url",
    )
    if not path:
        return None

    filename = meta.get("filename")
    stem = Path(str(filename)).stem if isinstance(filename, str) and filename else f"easyread_{doc_id[:8]}"
    download_name = f"{stem}_easyread.pdf"
    return path, download_name


def delete_user_project(user_id: str, doc_id: str) -> bool:
    meta = _load_meta(user_id, doc_id)
    urls_to_delete: list[str] = []
    for key, value in meta.items():
        if key.endswith("_blob_url") and isinstance(value, str) and value.strip():
            urls_to_delete.append(value)

    # Delete deterministic objects as a fallback even when blob URL keys are absent.
    for name in (
        "metadata.json",
        "summary.txt",
        "translation.txt",
        "easyread.txt",
        "easyread.docx",
        "easyread.pdf",
        meta.get("source_file") if isinstance(meta.get("source_file"), str) else "",
    ):
        if name:
            urls_to_delete.append(blob_storage.public_url_for(_blob_pathname(user_id, doc_id, name)))

    if urls_to_delete:
        blob_storage.delete_urls(sorted(set(urls_to_delete)))

    project_dir = _project_dir(user_id, doc_id)
    if not project_dir.is_dir():
        return False
    shutil.rmtree(project_dir, ignore_errors=True)
    return True
