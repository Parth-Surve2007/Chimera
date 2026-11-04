"""Approval-gated creation and structural verification of genuine PDF files."""
from __future__ import annotations

import hashlib
import re
from html import escape
from pathlib import Path


def _normalise_extracted_text(value: str) -> str:
    return " ".join(str(value or "").replace("\u00a0", " ").split())


def _pdf_font_name() -> str:
    """Prefer ReportLab's bundled Vera TTF for broader Unicode coverage."""
    try:
        import reportlab
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont

        font_path = Path(reportlab.__file__).resolve().parent / "fonts" / "Vera.ttf"
        if font_path.is_file():
            if "ChimeraVera" not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont("ChimeraVera", str(font_path)))
            return "ChimeraVera"
    except Exception:
        pass
    return "Helvetica"


def create_pdf(path: Path, *, title: str, content: str) -> dict:
    """Write a new PDF, reopen it, and verify its extracted text and page count.

    The target is opened with exclusive-create semantics. A failed write removes
    only the file created by this invocation, never a pre-existing destination.
    """
    target = Path(path)
    if not isinstance(title, str) or not title.strip():
        raise ValueError("PDF title must be a non-empty string")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("PDF content must be a non-empty string")
    encoded = content.encode("utf-8")
    if len(encoded) > 2_000_000:
        raise ValueError("PDF content exceeds the 2 MB limit")

    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    font_name = _pdf_font_name()
    title_style = ParagraphStyle(
        "ChimeraPDFTitle", fontName=font_name, fontSize=17, leading=22,
        textColor=colors.HexColor("#202020"), alignment=TA_LEFT,
        spaceAfter=10,
    )
    body_style = ParagraphStyle(
        "ChimeraPDFBody", fontName=font_name, fontSize=9.5, leading=13,
        textColor=colors.HexColor("#242424"), alignment=TA_LEFT,
        splitLongWords=1, allowWidows=0, allowOrphans=0,
        spaceAfter=3,
    )
    story = [Paragraph(escape(title.strip()), title_style)]
    # Use paragraphs, not raw PDF operators, so markup characters in source
    # text remain literal and long lines wrap naturally on the page.
    for line in content.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line:
            story.append(Paragraph(escape(line), body_style))
        else:
            story.append(Spacer(1, 3))
    target.parent.mkdir(parents=True, exist_ok=True)

    created_by_us = False
    try:
        with target.open("xb") as output:
            created_by_us = True
            document = SimpleDocTemplate(
                output, pagesize=A4,
                rightMargin=20 * mm, leftMargin=20 * mm,
                topMargin=18 * mm, bottomMargin=18 * mm,
                title=title.strip(), author="Chimera",
                subject="Verified text export",
            )
            document.build(story)

        from pypdf import PdfReader
        data = target.read_bytes()
        if not data.startswith(b"%PDF-"):
            raise ValueError("PDF signature is missing")
        reader = PdfReader(str(target), strict=True)
        if reader.is_encrypted:
            raise ValueError("generated PDF unexpectedly requires a password")
        page_text = [page.extract_text() or "" for page in reader.pages]
        extracted = "\n".join(page_text)
        expected_normalised = _normalise_extracted_text(title.strip() + "\n" + content)
        actual_normalised = _normalise_extracted_text(extracted)
        # Some PDF extractors insert line breaks in the middle of visual lines.
        # Whitespace normalization preserves words and punctuation for comparison.
        if expected_normalised != actual_normalised:
            raise ValueError(
                "PDF was generated but its extracted text does not match the requested source text"
            )
        if not reader.pages:
            raise ValueError("generated PDF contains no pages")
        return {
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "format": "pdf",
            "page_count": len(reader.pages),
            "text_verified": True,
            "extracted_text_sha256": hashlib.sha256(actual_normalised.encode("utf-8")).hexdigest(),
        }
    except Exception:
        if created_by_us:
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass
        raise
