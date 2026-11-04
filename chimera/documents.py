"""Minimal, dependency-free Word OOXML document writer."""
from __future__ import annotations

import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
CP = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
DC = "http://purl.org/dc/elements/1.1/"
APP = "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"

ET.register_namespace("w", W)
ET.register_namespace("r", R)
ET.register_namespace("", CT)


def _tag(namespace: str, name: str) -> str:
    return f"{{{namespace}}}{name}"


def _clean_text(value: str) -> str:
    return "".join(
        char for char in value
        if char in "\t\n\r" or ord(char) >= 0x20
    )


def _add_text_run(paragraph: ET.Element, text: str) -> None:
    run = ET.SubElement(paragraph, _tag(W, "r"))
    for index, line in enumerate(_clean_text(text).split("\n")):
        if index:
            ET.SubElement(run, _tag(W, "br"))
        node = ET.SubElement(run, _tag(W, "t"))
        if line[:1].isspace() or line[-1:].isspace():
            node.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        node.text = line


def _add_paragraph(body: ET.Element, text: str, style: str | None = None) -> None:
    paragraph = ET.SubElement(body, _tag(W, "p"))
    if style:
        properties = ET.SubElement(paragraph, _tag(W, "pPr"))
        ET.SubElement(properties, _tag(W, "pStyle"), {_tag(W, "val"): style})
    _add_text_run(paragraph, text)


def _document_xml(title: str, content: str) -> bytes:
    root = ET.Element(_tag(W, "document"))
    body = ET.SubElement(root, _tag(W, "body"))
    _add_paragraph(body, title, "Title")

    blocks = content.replace("\r\n", "\n").replace("\r", "\n").split("\n\n")
    for block in blocks:
        lines = [line.rstrip() for line in block.split("\n")]
        text = "\n".join(line for line in lines if line.strip())
        if not text:
            _add_paragraph(body, "")
            continue
        first = text.split("\n", 1)[0]
        if first.startswith("### "):
            _add_paragraph(body, text[4:], "Heading2")
        elif first.startswith("## "):
            _add_paragraph(body, text[3:], "Heading1")
        elif first.startswith("# "):
            _add_paragraph(body, text[2:], "Heading1")
        elif first.startswith(("- ", "* ")):
            _add_paragraph(body, "• " + text[2:])
        else:
            _add_paragraph(body, text)

    section = ET.SubElement(body, _tag(W, "sectPr"))
    ET.SubElement(section, _tag(W, "pgSz"), {
        _tag(W, "w"): "12240",
        _tag(W, "h"): "15840",
    })
    ET.SubElement(section, _tag(W, "pgMar"), {
        _tag(W, "top"): "1440",
        _tag(W, "right"): "1440",
        _tag(W, "bottom"): "1440",
        _tag(W, "left"): "1440",
        _tag(W, "header"): "720",
        _tag(W, "footer"): "720",
        _tag(W, "gutter"): "0",
    })
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _styles_xml() -> bytes:
    root = ET.Element(_tag(W, "styles"))
    defaults = ET.SubElement(root, _tag(W, "docDefaults"))
    run_defaults = ET.SubElement(defaults, _tag(W, "rPrDefault"))
    run_properties = ET.SubElement(run_defaults, _tag(W, "rPr"))
    ET.SubElement(run_properties, _tag(W, "rFonts"), {
        _tag(W, "ascii"): "Aptos",
        _tag(W, "hAnsi"): "Aptos",
    })
    size = ET.SubElement(run_properties, _tag(W, "sz"))
    size.set(_tag(W, "val"), "22")

    normal = ET.SubElement(root, _tag(W, "style"), {
        _tag(W, "type"): "paragraph",
        _tag(W, "default"): "1",
        _tag(W, "styleId"): "Normal",
    })
    ET.SubElement(normal, _tag(W, "name"), {_tag(W, "val"): "Normal"})

    title = ET.SubElement(root, _tag(W, "style"), {
        _tag(W, "type"): "paragraph",
        _tag(W, "styleId"): "Title",
    })
    ET.SubElement(title, _tag(W, "name"), {_tag(W, "val"): "Title"})
    title_ppr = ET.SubElement(title, _tag(W, "pPr"))
    ET.SubElement(title_ppr, _tag(W, "jc"), {_tag(W, "val"): "center"})
    title_rpr = ET.SubElement(title, _tag(W, "rPr"))
    ET.SubElement(title_rpr, _tag(W, "b"))
    ET.SubElement(title_rpr, _tag(W, "sz"), {_tag(W, "val"): "36"})

    for style_id, display_name, size_value in (
        ("Heading1", "heading 1", "30"),
        ("Heading2", "heading 2", "26"),
    ):
        style = ET.SubElement(root, _tag(W, "style"), {
            _tag(W, "type"): "paragraph",
            _tag(W, "styleId"): style_id,
        })
        ET.SubElement(style, _tag(W, "name"), {_tag(W, "val"): display_name})
        properties = ET.SubElement(style, _tag(W, "rPr"))
        ET.SubElement(properties, _tag(W, "b"))
        ET.SubElement(properties, _tag(W, "sz"), {_tag(W, "val"): size_value})
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def create_docx(path: Path, *, title: str, content: str) -> None:
    """Write a valid .docx package. Caller owns path validation and no-overwrite policy."""
    target = Path(path)
    payloads = {
        "[Content_Types].xml": f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="{CT}">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>""",
        "_rels/.rels": f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="{REL}">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>""",
        "word/_rels/document.xml.rels": f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="{REL}">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>""",
        "docProps/app.xml": f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="{APP}"><Application>Chimera</Application></Properties>""",
    }
    # Build core properties through ElementTree so titles are safely XML-escaped.
    core_root = ET.Element(_tag(CP, "coreProperties"))
    creator_title = ET.SubElement(core_root, _tag(DC, "title"))
    creator_title.text = _clean_text(title)
    creator = ET.SubElement(core_root, _tag(DC, "creator"))
    creator.text = "Chimera"
    payloads["docProps/core.xml"] = ET.tostring(core_root, encoding="utf-8", xml_declaration=True)
    payloads["word/document.xml"] = _document_xml(title, content)
    payloads["word/styles.xml"] = _styles_xml()

    try:
        with target.open("xb") as stream:
            with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name, data in payloads.items():
                    archive.writestr(name, data)
    except Exception:
        target.unlink(missing_ok=True)
        raise
