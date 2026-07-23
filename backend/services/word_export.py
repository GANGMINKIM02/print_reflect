from __future__ import annotations

"""이지리드 문서 Word(.docx) 내보내기.

역할: DocumentResponse를 Easy-Read 타이포그래피 규칙에 맞는 Word 파일로 변환한다.
주요 기능: export_to_docx — 소제목 + 항목별 글상자(투명 테두리) 2단(왼쪽 그림 / 오른쪽 본문).
관계: export_layout, image_assets, models/schemas, routers/documents(export API).
"""

import base64
import io
import re
import tempfile
from pathlib import Path

from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_ROW_HEIGHT_RULE
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt

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
from backend.services.image_matcher import MAX_IMAGES_PER_TEXT, find_images_for_line
from backend.services.rich_text import has_style_markers, iter_styled_runs

BODY_PT = 12
BOLD_BODY_PT = 12
HEADING_PT = 17
FOOTER_PT = 11
LINE_SPACING = 2.0  # 200% (이지리드 가이드)
PAGE_MARGIN_IN = 0.6
PAGE_MARGIN = Inches(PAGE_MARGIN_IN)
IMAGE_COL_IN = 2.05
GAP_COL_IN = 0.1
BODY_COL_IN = 8.5 - PAGE_MARGIN_IN * 2 - IMAGE_COL_IN - GAP_COL_IN
IMAGE_COL_WIDTH = Inches(IMAGE_COL_IN)
GAP_COL_WIDTH = Inches(GAP_COL_IN)
BODY_COL_WIDTH = Inches(BODY_COL_IN)
IMAGE_DISPLAY_WIDTH = Inches(1.85)
IMAGE_CELL_FILL = "FFFFFF"
CELL_PAD_V = 40  # twips — 셀 상하 여백
CELL_PAD_GAP = 48  # twips — 그림·본문 사이 간격

_SKIP_LINE = re.compile(r"^(---+\s*|\(\d+/\d+\s*쪽\)|\d+/\d+\s*쪽|>\s)")
_META_SECTION_START = re.compile(r"^###\s*수정\s*사항")


def _set_run_font(run, size_pt: float, bold: bool = False) -> None:
    run.font.name = "Malgun Gothic"
    run.font.size = Pt(size_pt)
    run.font.bold = bold
    r_pr = run._element.get_or_add_rPr()
    r_fonts = r_pr.find(qn("w:rFonts"))
    if r_fonts is None:
        r_fonts = OxmlElement("w:rFonts")
        r_pr.insert(0, r_fonts)
    r_fonts.set(qn("w:ascii"), "Malgun Gothic")
    r_fonts.set(qn("w:hAnsi"), "Malgun Gothic")
    r_fonts.set(qn("w:eastAsia"), "맑은 고딕")
    for tag in ("w:b", "w:bCs"):
        existing = r_pr.find(qn(tag))
        if bold:
            if existing is None:
                r_pr.append(OxmlElement(tag))
        elif existing is not None:
            r_pr.remove(existing)


def _is_heading(line: str) -> bool:
    s = line.strip()
    if s.startswith("<") and s.endswith(">"):
        return True
    if s.startswith("■"):
        return True
    return s.startswith("#")


def _clean_heading(line: str) -> str:
    return re.sub(r"^#+\s*", "", line.strip())


def _configure_section(section) -> None:
    section.top_margin = PAGE_MARGIN
    section.bottom_margin = PAGE_MARGIN
    section.left_margin = PAGE_MARGIN
    section.right_margin = PAGE_MARGIN
    _set_page_number_footer(section)


def _inches_to_twips(width: Inches) -> int:
    return int(width.inches * 1440)


def _set_page_number_footer(section) -> None:
    footer = section.footer
    fp = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
    fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    fp.clear()

    run_prefix = fp.add_run("- ")
    _set_run_font(run_prefix, FOOTER_PT)

    fld_begin = OxmlElement("w:fldChar")
    fld_begin.set(qn("w:fldCharType"), "begin")
    run_prefix._r.append(fld_begin)

    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = " PAGE "
    run_prefix._r.append(instr)

    fld_sep = OxmlElement("w:fldChar")
    fld_sep.set(qn("w:fldCharType"), "separate")
    run_prefix._r.append(fld_sep)

    run_num = fp.add_run("1")
    _set_run_font(run_num, FOOTER_PT)

    fld_end = OxmlElement("w:fldChar")
    fld_end.set(qn("w:fldCharType"), "end")
    run_num._r.append(fld_end)

    run_suffix = fp.add_run(" -")
    _set_run_font(run_suffix, FOOTER_PT)


def _remove_table_borders(table) -> None:
    tbl_pr = table._tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        element = OxmlElement(f"w:{edge}")
        element.set(qn("w:val"), "nil")
        borders.append(element)
    tbl_pr.append(borders)


def _set_cell_borders_transparent(cell) -> None:
    """글상자처럼 셀 테두리를 투명(nil) 처리."""
    tc_pr = cell._tc.get_or_add_tcPr()
    borders = OxmlElement("w:tcBorders")
    for edge in ("top", "left", "bottom", "right"):
        element = OxmlElement(f"w:{edge}")
        element.set(qn("w:val"), "nil")
        borders.append(element)
    tc_pr.append(borders)


def _set_cell_vertical_align(cell, *, top: bool = True) -> None:
    cell.vertical_alignment = (
        WD_CELL_VERTICAL_ALIGNMENT.TOP if top else WD_CELL_VERTICAL_ALIGNMENT.CENTER
    )


def _set_cell_margins(cell, *, top: int = 0, bottom: int = 0, left: int = 0, right: int = 0) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    tc_mar = OxmlElement("w:tcMar")
    for edge, value in (("top", top), ("left", left), ("bottom", bottom), ("right", right)):
        node = OxmlElement(f"w:{edge}")
        node.set(qn("w:w"), str(value))
        node.set(qn("w:type"), "dxa")
        tc_mar.append(node)
    tc_pr.append(tc_mar)


def _reset_cell_paragraphs(cell) -> None:
    while len(cell.paragraphs) > 1:
        cell._element.remove(cell.paragraphs[-1]._element)
    paragraph = cell.paragraphs[0]
    paragraph.clear()
    paragraph.paragraph_format.space_before = Pt(0)
    paragraph.paragraph_format.space_after = Pt(0)
    paragraph.paragraph_format.line_spacing_rule = WD_LINE_SPACING.SINGLE
    paragraph.paragraph_format.line_spacing = 1.0


def _set_cell_shading(cell, fill_hex: str) -> None:
    shading = OxmlElement("w:shd")
    shading.set(qn("w:fill"), fill_hex)
    shading.set(qn("w:val"), "clear")
    tc_pr = cell._tc.get_or_add_tcPr()
    tc_pr.append(shading)


def _set_column_widths(table, widths: list[Inches]) -> None:
    for row in table.rows:
        for idx, width in enumerate(widths):
            row.cells[idx].width = width


def _set_cell_width_fixed(cell, width: Inches) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    for child in list(tc_pr):
        if child.tag == qn("w:tcW"):
            tc_pr.remove(child)
    tc_w = OxmlElement("w:tcW")
    tc_w.set(qn("w:w"), str(_inches_to_twips(width)))
    tc_w.set(qn("w:type"), "dxa")
    tc_pr.insert(0, tc_w)


def _set_table_fixed_layout(table, widths: list[Inches]) -> None:
    """행마다 열 너비가 흔들리지 않도록 고정(그림 유무 본문 정렬 일치)."""
    tbl = table._tbl
    tbl_pr = tbl.tblPr
    for child in list(tbl_pr):
        if child.tag == qn("w:tblLayout"):
            tbl_pr.remove(child)
        if child.tag == qn("w:tblInd"):
            tbl_pr.remove(child)
    layout = OxmlElement("w:tblLayout")
    layout.set(qn("w:type"), "fixed")
    tbl_pr.append(layout)
    tbl_ind = OxmlElement("w:tblInd")
    tbl_ind.set(qn("w:w"), "0")
    tbl_ind.set(qn("w:type"), "dxa")
    tbl_pr.append(tbl_ind)

    for existing in tbl.findall(qn("w:tblGrid")):
        tbl.remove(existing)
    tbl_grid = OxmlElement("w:tblGrid")
    for width in widths:
        grid_col = OxmlElement("w:gridCol")
        grid_col.set(qn("w:w"), str(_inches_to_twips(width)))
        tbl_grid.append(grid_col)
    tbl_pr.addnext(tbl_grid)


def _resolve_placement_path(placement: ImagePlacement) -> Path | None:
    raw = (placement.image_base64 or "").strip()
    if raw:
        payload = raw.split(",", 1)[1] if raw.startswith("data:") else raw
        try:
            data = base64.b64decode(payload)
        except (ValueError, TypeError):
            return None
        suffix = ".png"
        lower = raw.lower()
        for ext in (".jpg", ".jpeg", ".webp", ".gif"):
            if ext in lower[:40]:
                suffix = ext
                break
        tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        tmp.write(data)
        tmp.close()
        return Path(tmp.name)
    return resolve_placement_image(
        image_file=placement.image_file,
        image_url=placement.image_url,
    )


def _apply_body_format(paragraph) -> None:
    paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
    paragraph.paragraph_format.first_line_indent = Pt(0)
    paragraph.paragraph_format.left_indent = Pt(0)
    paragraph.paragraph_format.right_indent = Pt(0)
    paragraph.paragraph_format.line_spacing_rule = WD_LINE_SPACING.MULTIPLE
    paragraph.paragraph_format.line_spacing = LINE_SPACING
    paragraph.paragraph_format.space_after = Pt(6)


def _add_runs_to_paragraph(paragraph, line: str, *, size_pt: float, bold_default: bool = False) -> None:
    for part, is_bold, run_pt in iter_styled_runs(line, default_pt=size_pt):
        run = paragraph.add_run(part)
        _set_run_font(run, run_pt, bold=is_bold or bold_default)


def _add_heading_paragraph(doc: Document, line: str) -> None:
    stripped = line.strip()
    if not stripped:
        return
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.LEFT
    p.paragraph_format.space_before = Pt(16)
    p.paragraph_format.space_after = Pt(8)
    p.paragraph_format.line_spacing_rule = WD_LINE_SPACING.MULTIPLE
    p.paragraph_format.line_spacing = LINE_SPACING
    raw = _clean_heading(stripped) if _is_heading(stripped) else stripped
    if has_style_markers(stripped):
        _add_runs_to_paragraph(p, stripped, size_pt=HEADING_PT)
    else:
        run = p.add_run(raw)
        _set_run_font(run, HEADING_PT, bold=True)


def _add_body_paragraph_to_cell(cell, line: str, *, first: bool = False) -> None:
    stripped = line.strip()
    if not stripped or _SKIP_LINE.match(stripped):
        return
    if is_image_placeholder(stripped):
        return
    if first and cell.paragraphs and not cell.paragraphs[0].text.strip():
        p = cell.paragraphs[0]
        p.clear()
    else:
        p = cell.add_paragraph()
    _apply_body_format(p)
    if first:
        p.paragraph_format.space_before = Pt(0)
    _add_runs_to_paragraph(p, stripped, size_pt=BODY_PT)


def _add_picture_to_cell(cell, placement: ImagePlacement) -> None:
    img_path = _resolve_placement_path(placement)
    if not img_path:
        return
    _reset_cell_paragraphs(cell)
    p = cell.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run()
    run.add_picture(str(img_path), width=IMAGE_DISPLAY_WIDTH)


def _add_item_text_boxes(
    doc: Document,
    placement: ImagePlacement | None,
    body_lines: list[str],
) -> None:
    """항목 1개 = (그림 | 본문) 2단. 그림 없으면 왼쪽 빈칸 유지(그림 탭과 동일)."""
    table = doc.add_table(rows=1, cols=2)
    table.autofit = False
    table.allow_autofit = False
    _remove_table_borders(table)
    col_widths = [IMAGE_COL_WIDTH, Inches(BODY_COL_IN + GAP_COL_IN)]
    _set_table_fixed_layout(table, col_widths)
    _set_column_widths(table, col_widths)

    row = table.rows[0]
    row.height_rule = WD_ROW_HEIGHT_RULE.AUTO
    image_cell = row.cells[0]
    body_cell = row.cells[1]
    _set_cell_width_fixed(image_cell, col_widths[0])
    _set_cell_width_fixed(body_cell, col_widths[1])

    for cell in (image_cell, body_cell):
        _set_cell_borders_transparent(cell)
        _set_cell_vertical_align(cell, top=True)
        _reset_cell_paragraphs(cell)

    if placement:
        _set_cell_shading(image_cell, "FFFFFF")
        _add_picture_to_cell(image_cell, placement)
    else:
        _set_cell_shading(image_cell, "FFFFFF")
        p = image_cell.paragraphs[0]
        run = p.add_run("\u00a0")
        _set_run_font(run, BODY_PT)

    _set_cell_shading(body_cell, "FFFFFF")
    _set_cell_margins(image_cell, top=CELL_PAD_V, bottom=CELL_PAD_V, left=0, right=CELL_PAD_GAP)
    _set_cell_margins(body_cell, top=CELL_PAD_V, bottom=CELL_PAD_V, left=CELL_PAD_GAP, right=0)

    first = True
    for line in body_lines:
        _add_body_paragraph_to_cell(body_cell, line, first=first)
        first = False

    spacer = doc.add_paragraph()
    spacer.paragraph_format.space_before = Pt(0)
    spacer.paragraph_format.space_after = Pt(12)


def _add_closing_paragraph(doc: Document, line: str) -> None:
    stripped = line.strip()
    if not stripped:
        return
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(12)
    _apply_body_format(p)
    _add_runs_to_paragraph(p, stripped, size_pt=BODY_PT)


def _export_easy_read_layout(
    doc: Document,
    text: str,
    placements: list[ImagePlacement],
) -> None:
    """작성양식 PDF: <소제목> + 항목별 (삽화 | 글). 마무리 문장은 2단 밖 전체 너비."""
    body, closing = split_standard_closing(text)
    by_item = align_placements_one_per_section(body, placements)

    for section in parse_export_sections(body):
        if section.heading:
            _add_heading_paragraph(doc, section.heading)
        for item in parse_section_items(section):
            placement = by_item.get(item.start_line_index)
            if placement is not None and not isinstance(placement, ImagePlacement):
                placement = ImagePlacement(**placement)  # type: ignore[arg-type]
            if placement:
                blocks = split_item_lines_into_blocks(item.lines)
                _add_item_text_boxes(doc, placement, blocks[0])
                for block in blocks[1:]:
                    _add_item_text_boxes(doc, None, block)
            else:
                _add_item_text_boxes(doc, None, item.lines)

    if closing:
        _add_closing_paragraph(doc, closing)


def _add_rich_paragraph(doc: Document, line: str) -> None:
    stripped = line.strip()
    if not stripped or _SKIP_LINE.match(stripped):
        return
    if stripped == "## 수정된 이지리드 번역본":
        return
    if is_image_placeholder(stripped):
        return

    if _is_heading(stripped):
        _add_heading_paragraph(doc, stripped)
        return

    p = doc.add_paragraph()
    _apply_body_format(p)
    _add_runs_to_paragraph(p, stripped, size_pt=BODY_PT)


def _add_picture(doc: Document, image_file: str, image_url: str | None = None) -> None:
    img_path = resolve_placement_image(image_file=image_file, image_url=image_url)
    if not img_path:
        return
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.LEFT
    run = p.add_run()
    run.add_picture(str(img_path), width=IMAGE_DISPLAY_WIDTH)


def _export_text(doc: Document, text: str, *, skip_meta: bool = True) -> None:
    in_meta_section = False
    inserted_images: set[str] = set()

    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if skip_meta and stripped.startswith("원본 파일:"):
            continue
        if skip_meta and stripped == "## 수정된 이지리드 번역본":
            continue
        if skip_meta and _META_SECTION_START.match(stripped):
            in_meta_section = True
            continue
        if in_meta_section:
            continue

        _add_rich_paragraph(doc, line)

        if len(inserted_images) >= MAX_IMAGES_PER_TEXT:
            continue
        for match in find_images_for_line(
            stripped,
            exclude=inserted_images,
            max_total=MAX_IMAGES_PER_TEXT,
        ):
            _add_picture(doc, match.image_file)
            inserted_images.add(match.image_file)


def _collect_placements(doc: DocumentResponse) -> list[ImagePlacement] | None:
    for segment in doc.translation_segments or []:
        if segment.image_placements:
            return segment.image_placements
    return None


def _collect_body_text(doc: DocumentResponse) -> str:
    if doc.translation_segments:
        for segment in doc.translation_segments:
            if segment.image_placements and segment.easy_text and segment.easy_text.strip():
                return segment.easy_text.strip()
    if doc.translation_text and doc.translation_text.strip():
        return doc.translation_text.strip()
    if doc.translation_segments:
        parts = [
            s.easy_text.strip()
            for s in doc.translation_segments
            if s.easy_text and s.easy_text.strip()
        ]
        if parts:
            return max(parts, key=len)
    if doc.summary and doc.summary.strip():
        return doc.summary.strip()
    return ""


def export_to_docx(doc: DocumentResponse, *, include_meta: bool = False) -> bytes:
    word = Document()

    for section in word.sections:
        _configure_section(section)

    if include_meta:
        title = word.add_heading("이지리드 판결문", level=0)
        for run in title.runs:
            _set_run_font(run, 18, bold=True)
        meta = word.add_paragraph(f"원본 파일: {doc.filename}  |  유형: {doc.doc_type}")
        meta.alignment = WD_ALIGN_PARAGRAPH.LEFT

    body = _collect_body_text(doc)
    if body:
        raw_placements = _collect_placements(doc) or []
        placements = prepare_placements_for_export(body, raw_placements)
        sections = parse_export_sections(body)
        if any(section.heading for section in sections):
            _export_easy_read_layout(word, body, placements)
        else:
            _export_text(word, body, skip_meta=not include_meta)

    buffer = io.BytesIO()
    word.save(buffer)
    return buffer.getvalue()
