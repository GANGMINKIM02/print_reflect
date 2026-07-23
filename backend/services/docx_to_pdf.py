from __future__ import annotations

"""Word(.docx) → PDF 변환.

Word export 레이아웃을 그대로 유지하기 위해 HTML/PyMuPDF 대신
완성된 docx를 PDF로 변환한다 (Word COM·LibreOffice·선택적 ConvertAPI).
"""

import base64
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx

from backend.config import IS_VERCEL, settings

logger = logging.getLogger(__name__)


class DocxToPdfError(RuntimeError):
    """모든 변환 백엔드가 실패했을 때."""


def convert_docx_bytes_to_pdf(docx_bytes: bytes) -> bytes:
    return convert_file_bytes_to_pdf(docx_bytes, "export.docx")


def convert_file_bytes_to_pdf(source_bytes: bytes, filename: str) -> bytes:
    source_suffix = Path(filename).suffix.lower() or ".docx"
    errors: list[str] = []
    for name, converter in _available_backends():
        try:
            pdf = converter(source_bytes, source_suffix)
            if pdf:
                logger.info("file→pdf via %s (%d bytes)", name, len(pdf))
                return pdf
        except Exception as exc:  # noqa: BLE001 — try next backend
            logger.warning("file→pdf backend %s failed: %s", name, exc)
            errors.append(f"{name}: {exc}")
    raise DocxToPdfError("; ".join(errors) or "no converter available")


def _available_backends():
    backends: list[tuple[str, object]] = []
    if settings.convertapi_secret.strip():
        backends.append(("convertapi", _convert_via_convertapi))
    if sys.platform in ("win32", "darwin"):
        backends.append(("docx2pdf", _convert_via_docx2pdf))
    if _find_libreoffice():
        backends.append(("libreoffice", _convert_via_libreoffice))
    return backends


def _find_libreoffice() -> str | None:
    for name in ("soffice", "libreoffice", "soffice.exe"):
        found = shutil.which(name)
        if found:
            return found
    for candidate in (
        Path(r"C:\Program Files\LibreOffice\program\soffice.exe"),
        Path("/usr/bin/libreoffice"),
        Path("/usr/bin/soffice"),
    ):
        if candidate.is_file():
            return str(candidate)
    return None


def _write_temp_source(source_bytes: bytes, directory: Path, suffix: str) -> Path:
    source_path = directory / f"export{suffix}"
    source_path.write_bytes(source_bytes)
    return source_path


def _read_pdf_output(source_path: Path, directory: Path) -> bytes:
    pdf_path = directory / f"{source_path.stem}.pdf"
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF not created: {pdf_path.name}")
    return pdf_path.read_bytes()


def _convert_via_docx2pdf(source_bytes: bytes, source_suffix: str = ".docx") -> bytes:
    from docx2pdf import convert

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        source_path = _write_temp_source(source_bytes, tmp_dir, source_suffix)
        pdf_path = tmp_dir / f"{source_path.stem}.pdf"
        convert(str(source_path), str(pdf_path))
        return pdf_path.read_bytes()


def _convert_via_libreoffice(source_bytes: bytes, source_suffix: str = ".docx") -> bytes:
    binary = _find_libreoffice()
    if not binary:
        raise FileNotFoundError("LibreOffice not found")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        docx_path = _write_temp_source(source_bytes, tmp_dir, source_suffix)
        profile_dir = tmp_dir / "lo-profile"
        profile_dir.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env["HOME"] = str(tmp_dir)
        cmd = [
            binary,
            "--headless",
            "--nologo",
            "--nofirststartwizard",
            "--norestore",
            f"-env:UserInstallation=file:///{profile_dir.as_posix()}",
            "--convert-to",
            "pdf:writer_pdf_Export",
            "--outdir",
            str(tmp_dir),
            str(docx_path),
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "LibreOffice failed")
        return _read_pdf_output(docx_path, tmp_dir)


def _convert_via_convertapi(source_bytes: bytes, source_suffix: str = ".docx") -> bytes:
    secret = settings.convertapi_secret.strip()
    if not secret:
        raise ValueError("CONVERTAPI_SECRET is empty")

    endpoint = {".doc": "doc", ".docx": "docx"}.get(source_suffix.lower(), "docx")
    content_type = {
        ".doc": "application/msword",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }.get(source_suffix.lower(), "application/octet-stream")

    response = httpx.post(
        f"https://v2.convertapi.com/convert/{endpoint}/to/pdf",
        headers={"Authorization": f"Bearer {secret}"},
        files={
            "File": (
                f"easyread{source_suffix or '.docx'}",
                source_bytes,
                content_type,
            ),
        },
        timeout=120.0,
    )
    response.raise_for_status()
    payload = response.json()
    files = payload.get("Files") or []
    if not files:
        raise RuntimeError("ConvertAPI returned no files")
    file_data = files[0].get("FileData")
    if file_data:
        return base64.b64decode(file_data)
    file_url = files[0].get("Url")
    if not file_url:
        raise RuntimeError("ConvertAPI response missing FileData/Url")
    pdf_response = httpx.get(file_url, timeout=120.0, follow_redirects=True)
    pdf_response.raise_for_status()
    return pdf_response.content


def conversion_backend_hint() -> str:
    if settings.convertapi_secret.strip():
        return "convertapi"
    if sys.platform in ("win32", "darwin"):
        return "docx2pdf"
    if _find_libreoffice():
        return "libreoffice"
    if IS_VERCEL:
        return "convertapi-required"
    return "unavailable"
