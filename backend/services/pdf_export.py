from __future__ import annotations

"""이지리드 문서 PDF 내보내기.

역할: Word(.docx) export 결과를 PDF로 변환해 미리보기·다운로드에 사용한다.
주요 기능: export_to_pdf — Word 완성 → docx→pdf (실패 시 HTML/PyMuPDF 폴백).
관계: word_export, docx_to_pdf, routers/documents.
"""

import base64
import html
import io
import logging
import mimetypes
import os
import re
import textwrap
from pathlib import Path

import fitz  # PyMuPDF
from docx import Document

logger = logging.getLogger(__name__)

from backend.models.schemas import DocumentResponse, ImagePlacement
from backend.services.export_layout import (
    align_placements_one_per_section,
    is_image_placeholder,
    parse_export_sections,
    parse_section_items,
    prepare_placements_for_export,
    split_item_lines_into_blocks,
)
from backend.services.easy_read_sanitize import split_standard_closing
from backend.services.image_assets import resolve_placement_image
from backend.services.rich_text import iter_styled_runs
from backend.services.image_matcher import MAX_IMAGES_PER_TEXT, find_images_for_line
from backend.services.word_export import (
    _META_SECTION_START,
    _SKIP_LINE,
    _clean_heading,
    _collect_body_text,
    _collect_placements,
    _is_heading,
)

FONT_URL = (
    "https://github.com/google/fonts/raw/main/ofl/nanumgothic/NanumGothic-Regular.ttf"
)
PAGE_WIDTH = 595.28
PAGE_HEIGHT = 841.89
MARGIN = 43  # 0.6in — word_export PAGE_MARGIN 과 맞춤
CONTENT_WIDTH = PAGE_WIDTH - 2 * MARGIN
IMAGE_COL_PT = round(CONTENT_WIDTH * 0.32)
IMAGE_INSET_PT = IMAGE_COL_PT - 12
IMAGE_MAX_HEIGHT_PT = 130

_BOLD = re.compile(r"\*\*(.+?)\*\*")


def _font_dir() -> Path:
    bundled_dir = Path(__file__).resolve().parent.parent / "assets" / "fonts"
    bundled = bundled_dir / "NanumGothic-Regular.ttf"
    if bundled.exists():
        return bundled_dir
    cache_dir = Path(os.environ.get("TMPDIR", os.environ.get("TEMP", "/tmp")))
    cached = cache_dir / "nanumgothic-regular.ttf"
    if not cached.exists():
        import httpx

        response = httpx.get(FONT_URL, timeout=30.0, follow_redirects=True)
        response.raise_for_status()
        cached.write_bytes(response.content)
    return cache_dir


def _font_css() -> tuple[str, fitz.Archive]:
    font_dir = _font_dir()
    archive = fitz.Archive(str(font_dir))
    css = f"""
    @font-face {{
      font-family: "Nanum Gothic";
      src: url("nanumgothic-regular.ttf");
    }}
    body {{
      font-family: "Nanum Gothic", sans-serif;
      font-size: 12px;
      line-height: 2;
      color: #21272a;
    }}
    p.body {{
      margin: 0 0 8px 0;
    }}
    p.heading {{
      margin: 16px 0 8px 0;
      font-size: 17px;
      font-weight: bold;
    }}
    p.form-header {{
      margin: 0 0 12px 0;
      font-size: 17px;
      font-weight: bold;
    }}
    div.section-row {{
      width: 100%;
      overflow: hidden;
      margin: 0 0 20px 0;
    }}
    div.image-col {{
      float: left;
      width: {IMAGE_COL_PT}pt;
      background: #ffffff;
      padding: 8px 8px 8px 0;
      box-sizing: border-box;
    }}
    img.section-img {{
      width: {IMAGE_INSET_PT}pt;
      max-height: {IMAGE_MAX_HEIGHT_PT}pt;
      height: auto;
      display: block;
    }}
    div.image-empty {{
      min-height: {IMAGE_MAX_HEIGHT_PT}pt;
    }}
    div.body-col {{
      margin-left: {IMAGE_COL_PT}pt;
      padding: 8px 0 8px 4px;
    }}
    p.image-block {{
      margin: 8px 0 12px 0;
    }}
    p.image-block img {{
      max-width: 230px;
      height: auto;
    }}
    div.item-full-width {{
      clear: both;
      width: 100%;
      margin: 0 0 20px 0;
    }}
    p.closing-line {{
      clear: both;
      margin: 12px 0 0 0;
    }}
    """
    return css, archive


def _line_to_html(line: str) -> str | None:
    stripped = line.strip()
    if not stripped or _SKIP_LINE.match(stripped):
        return None
    if stripped == "## 수정된 이지리드 번역본":
        return None
    if is_image_placeholder(stripped):
        return None

    if _is_heading(stripped):
        text = html.escape(_clean_heading(stripped))
        return f'<p class="heading">{text}</p>'

    chunks: list[str] = []
    for part, is_bold, size_pt in iter_styled_runs(stripped):
        escaped = html.escape(part)
        inner = f"<strong>{escaped}</strong>" if is_bold else escaped
        if size_pt != 12:
            inner = f'<span style="font-size:{int(size_pt)}px">{inner}</span>'
        chunks.append(inner)
    return f'<p class="body">{"".join(chunks)}</p>'


def _lines_to_html(lines: list[str]) -> str:
    return "".join(block for line in lines if (block := _line_to_html(line)))


def _image_to_img_tag(image_file: str, image_url: str | None = None) -> str | None:
    img_path = resolve_placement_image(image_file=image_file, image_url=image_url)
    if not img_path:
        return None
    mime = mimetypes.guess_type(str(img_path))[0] or "image/png"
    encoded = base64.b64encode(img_path.read_bytes()).decode("ascii")
    return f'<img src="data:{mime};base64,{encoded}" alt="" />'


def _placement_to_img_tag(placement: ImagePlacement) -> str | None:
    raw = (placement.image_base64 or "").strip()
    if raw:
        src = raw if raw.startswith("data:") else f"data:image/png;base64,{raw}"
        return (
            f'<img class="section-img" src="{src}" '
            f'width="{IMAGE_INSET_PT}" alt="" />'
        )
    file_tag = _image_to_img_tag(placement.image_file, placement.image_url)
    if not file_tag:
        return None
    return file_tag.replace("<img ", f'<img class="section-img" width="{IMAGE_INSET_PT}" ', 1)


def _item_row_html(
    lines: list[str],
    placement: ImagePlacement | None,
) -> str:
    """항목별 (삽화 | 글) 2단 — 그림 없어도 왼쪽 빈칸 유지."""
    body_html = _lines_to_html(lines)
    if not body_html:
        return ""

    img_tag: str | None = None
    if placement:
        img_tag = _placement_to_img_tag(placement)

    image_cell = img_tag if img_tag else '<div class="image-empty">&nbsp;</div>'
    return (
        '<div class="section-row">'
        f'<div class="image-col">{image_cell}</div>'
        f'<div class="body-col">{body_html}</div>'
        "</div>"
    )


def _section_block_html(
    section,
    by_item: dict[int, ImagePlacement],
) -> str:
    """소제목 + 항목마다 (삽화 | 글) — 작성양식 PDF 구조."""
    blocks: list[str] = []
    if section.heading:
        heading_html = _line_to_html(section.heading)
        if heading_html:
            blocks.append(heading_html)

    for item in parse_section_items(section):
        placement = by_item.get(item.start_line_index)
        if placement is not None and not isinstance(placement, ImagePlacement):
            placement = ImagePlacement(**placement)  # type: ignore[arg-type]
        if placement:
            line_blocks = split_item_lines_into_blocks(item.lines)
            row = _item_row_html(line_blocks[0], placement)
            if row:
                blocks.append(row)
            for block in line_blocks[1:]:
                follow = _item_row_html(block, None)
                if follow:
                    blocks.append(follow)
        else:
            row = _item_row_html(item.lines, None)
            if row:
                blocks.append(row)

    return "".join(blocks)


def _build_html(doc: DocumentResponse) -> tuple[str, str]:
    body = _collect_body_text(doc)
    if not body:
        css, _ = _font_css()
        return "<body></body>", css

    export_body, closing = split_standard_closing(body)
    blocks: list[str] = []
    sections = parse_export_sections(export_body)
    raw_placements = _collect_placements(doc) or []
    placements = prepare_placements_for_export(export_body, raw_placements)
    has_section_layout = any(section.heading for section in sections)

    if has_section_layout:
        by_item_raw = align_placements_one_per_section(export_body, placements)
        by_item = {
            k: (v if isinstance(v, ImagePlacement) else ImagePlacement(**v))  # type: ignore[arg-type]
            for k, v in by_item_raw.items()
        }
        for section in sections:
            section_html = _section_block_html(section, by_item)
            if section_html:
                blocks.append(section_html)
    elif placements:
        by_item_raw = align_placements_one_per_section(export_body, placements)
        by_item = {
            k: (v if isinstance(v, ImagePlacement) else ImagePlacement(**v))  # type: ignore[arg-type]
            for k, v in by_item_raw.items()
        }
        for section in sections:
            section_html = _section_block_html(section, by_item)
            if section_html:
                blocks.append(section_html)
    else:
        in_meta_section = False
        inserted_images: set[str] = set()
        for line in export_body.split("\n"):
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("원본 파일:"):
                continue
            if stripped == "## 수정된 이지리드 번역본":
                continue
            if _META_SECTION_START.match(stripped):
                in_meta_section = True
                continue
            if in_meta_section:
                continue

            line_html = _line_to_html(line)
            if line_html:
                blocks.append(line_html)

            if len(inserted_images) >= MAX_IMAGES_PER_TEXT:
                continue
            for match in find_images_for_line(
                stripped,
                exclude=inserted_images,
                max_total=MAX_IMAGES_PER_TEXT,
            ):
                tag = _image_to_img_tag(match.image_file)
                if tag:
                    blocks.append(f'<p class="image-block">{tag}</p>')
                    inserted_images.add(match.image_file)

    css, _ = _font_css()
    if closing:
        closing_html = _line_to_html(closing)
        if closing_html:
            blocks.append(closing_html.replace('class="body"', 'class="body closing-line"', 1))
    content = "\n".join(blocks)
    return f"<html><head></head><body>{content}</body></html>", css


def export_to_pdf(doc: DocumentResponse) -> bytes:
    """Word export → PDF 변환. Word/LibreOffice 없으면 DocxToPdfError."""
    from backend.services import word_export
    from backend.services.docx_to_pdf import convert_docx_bytes_to_pdf

    docx_bytes = word_export.export_to_docx(doc)
    return convert_docx_bytes_to_pdf(docx_bytes)


def export_to_pdf_without_docx_converter(doc: DocumentResponse) -> bytes:
    """Converter-free fallback PDF rendering using plain text pagination."""
    body = _collect_body_text(doc) or doc.translation_text or doc.summary or ""
    if not body.strip():
        body = "(내보낼 내용이 없습니다)"

    return export_text_to_pdf(body)


def export_text_to_pdf(text: str) -> bytes:
    """Render plain Korean text into a PDF without external docx converters."""
    body = text if text.strip() else "(내보낼 내용이 없습니다)"

    content_rect = fitz.Rect(MARGIN, MARGIN, PAGE_WIDTH - MARGIN, PAGE_HEIGHT - MARGIN)
    font_size = 11
    line_height = 17
    wrap_width = 92
    lines_per_page = max(1, int(content_rect.height // line_height))

    lines: list[str] = []
    for raw in body.splitlines():
        line = raw.rstrip()
        if not line:
            lines.append("")
            continue
        wrapped = textwrap.wrap(
            line,
            width=wrap_width,
            break_long_words=True,
            replace_whitespace=False,
            drop_whitespace=False,
        )
        lines.extend(wrapped or [line])

    pdf = fitz.open()
    font_name = "nanumgothic"
    font_file: Path | None = None
    try:
        font_dir = _font_dir()
        for candidate in ("NanumGothic-Regular.ttf", "nanumgothic-regular.ttf"):
            path = font_dir / candidate
            if path.is_file():
                font_file = path
                break
    except Exception:
        font_file = None

    try:
        for start in range(0, len(lines), lines_per_page):
            page = pdf.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
            if font_file is not None:
                page.insert_font(fontname=font_name, fontfile=str(font_file))
            chunk = "\n".join(lines[start : start + lines_per_page])
            page.insert_textbox(
                content_rect,
                chunk,
                fontsize=font_size,
                fontname=font_name if font_file is not None else "helv",
                lineheight=line_height / font_size,
                color=(0.13, 0.15, 0.16),
                align=fitz.TEXT_ALIGN_LEFT,
            )
        return pdf.tobytes(garbage=3, deflate=True)
    finally:
        pdf.close()


def export_docx_bytes_to_pdf_without_converter(docx_bytes: bytes) -> bytes:
    """Render text from DOCX bytes to PDF when docx->pdf converters are unavailable."""
    doc = Document(io.BytesIO(docx_bytes))
    lines = [p.text.strip() for p in doc.paragraphs if p.text and p.text.strip()]
    text = "\n".join(lines).strip()
    return export_text_to_pdf(text)
