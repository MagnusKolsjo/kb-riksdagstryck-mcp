# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Magnus Kolsjö
# Se LICENSE-filen i repots rot för fullständig licenstext.

"""
05_pdf_to_xml.py — Konverterar KB:s PDF-volymer till ABBYY FineReader 10 XML

KB:s 16 PDF-only volymer (1746–1847) saknar XML men är sökbara — de har
ett OCR-textlager producerat av ABBYY FineReader, samma verktyg som
genererade XML-filerna för övriga 1 172 volymer.

PyMuPDF extraherar text med positionsdata (bounding boxes) per sida,
vilket vi rekonstruerar till ABBYY FineReader 10 XML med exakt samma
schema, taggar och attribut som KB:s egna filer. Resultatet sparas i
xml_raw/ och kan indexeras av 04_parse_and_index.py utan specialhantering.

Användning:
  python3 05_pdf_to_xml.py                    # konvertera alla PDF:er i pdf_raw/
  python3 05_pdf_to_xml.py --file pdf_raw/pr_1746-1747.pdf  # en specifik fil
  python3 05_pdf_to_xml.py --dry-run          # visa vad som skulle konverteras

Krav:
  pip install -r requirements.txt  (inkluderar pymupdf)
  pdf_raw/ måste innehålla nedladdade PDF-filer (kör 02_download_xml.py först)
"""

import argparse
import logging
import sys
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.dom import minidom

import fitz  # PyMuPDF

OUTPUT_DIR = Path(__file__).parent
PDF_RAW    = OUTPUT_DIR / "pdf_raw"
XML_RAW    = OUTPUT_DIR / "xml_raw"

# ABBYY FineReader 10 namnrymd — identisk med KB:s egna filer
ABBYY_NS  = "http://www.abbyy.com/FineReader_xml/FineReader10-schema-v1.xml"
ABBYY_XSD = "http://www.abbyy.com/FineReader_xml/FineReader10-schema-v1.xml FineReader10-schema-v1.xml"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ── Koordinathjälp ─────────────────────────────────────────────────────────────

def bbox_attrs(rect) -> dict:
    """Omvandlar ett fitz.Rect till ABBYY-koordinatattribut (l, t, r, b)."""
    return {
        "l": str(int(rect.x0)),
        "t": str(int(rect.y0)),
        "r": str(int(rect.x1)),
        "b": str(int(rect.y1)),
    }


# ── XML-byggare ────────────────────────────────────────────────────────────────

def tag(parent, name, attribs=None, text=None):
    """Skapar ett XML-element i ABBYY-namnrymden."""
    el = ET.SubElement(parent, f"{{{ABBYY_NS}}}{name}", attribs or {})
    if text is not None:
        el.text = text
    return el


def build_document_xml(pdf_path: Path) -> ET.Element:
    """
    Öppnar en PDF och bygger ett ABBYY FineReader 10 XML-dokument.

    Struktur (identisk med KB:s egna XML-filer):
      document
        documentData
          sections
            section
              stream [@role @beginPage @endPage]
                mainText [@columnCount]
                elemId [@id]
        page [@width @height @resolution] (en per PDF-sida)
          block [@blockType @l @t @r @b]
            region
              rect [@l @t @r @b]
            text [@id]
              par [@align @leftIndent]
                line [@baseline @l @t @r @b]
                  formatting [@lang]  ← texten sitter här
    """
    doc = fitz.open(str(pdf_path))
    n_pages = len(doc)

    # Rot: <document>
    ET.register_namespace("", ABBYY_NS)
    root = ET.Element(f"{{{ABBYY_NS}}}document", {
        "version":        "10",
        "producer":       "pdf_to_xml.py (rekonstruerad från KB sökbar PDF)",
        "pagesCount":     str(n_pages),
        "languages":      "Swedish",
        "schemaLocation": ABBYY_XSD,
    })

    # <documentData> / <sections> / <section>
    doc_data = tag(root, "documentData")
    sections = tag(doc_data, "sections")
    section  = tag(sections, "section")
    stream   = tag(section, "stream", {
        "role":      "text",
        "beginPage": "1",
        "endPage":   str(n_pages),
    })
    tag(stream, "mainText", {"columnCount": "1"})
    tag(stream, "elemId", {"id": "1"})

    text_id = 1

    for page_num, page in enumerate(doc, start=1):
        width  = int(page.rect.width)
        height = int(page.rect.height)

        page_el = tag(root, "page", {
            "width":      str(width),
            "height":     str(height),
            "resolution": "300",
        })

        # Hämta textblock med positionsdata från PyMuPDF
        # get_text("dict") returnerar blocks → lines → spans med bbox
        page_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)

        for block in page_dict.get("blocks", []):
            btype = block.get("type", -1)

            # Typ 1 = bild — skapa Picture-block utan text
            if btype == 1:
                block_el = tag(page_el, "block", {"blockType": "Picture"} | bbox_attrs(fitz.Rect(block["bbox"])))
                tag(block_el, "region").append(ET.Element(f"{{{ABBYY_NS}}}rect", bbox_attrs(fitz.Rect(block["bbox"]))))
                continue

            # Typ 0 = text
            if btype != 0:
                continue

            lines_data = block.get("lines", [])
            if not lines_data:
                continue

            block_rect  = fitz.Rect(block["bbox"])
            block_el    = tag(page_el, "block", {"blockType": "Text"} | bbox_attrs(block_rect))
            region      = tag(block_el, "region")
            tag(region, "rect", bbox_attrs(block_rect))

            text_el = tag(block_el, "text", {"id": str(text_id)})
            text_id += 1

            # Gruppera rader i stycken: ny stycke när vertikal lucka > 1.5× radavstånd
            prev_bottom   = None
            line_height   = None
            current_par   = None

            for line in lines_data:
                line_rect = fitz.Rect(line["bbox"])

                # Beräkna radavstånd
                if prev_bottom is not None and line_height is not None:
                    gap = line_rect.y0 - prev_bottom
                    new_para = gap > line_height * 1.5
                else:
                    new_para = True

                if new_para:
                    current_par = tag(text_el, "par", {
                        "align":      "Justified",
                        "leftIndent": "0",
                    })

                # Beräkna baseline (underkant av första span, eller b-koordinat)
                spans     = line.get("spans", [])
                baseline  = str(int(line_rect.y1))

                line_el = tag(current_par, "line", {
                    "baseline": baseline,
                } | bbox_attrs(line_rect))

                # Samla text från alla spans på raden
                line_text = ""
                for span in spans:
                    line_text += span.get("text", "")

                # Ta bort mjuka bindestreck (¬ / soft hyphen) inom raden
                line_text = line_text.replace("¬", "").replace("­", "")
                line_text = line_text.strip()

                if line_text:
                    fmt = tag(line_el, "formatting", {"lang": "Swedish"})
                    fmt.text = line_text

                if line_height is None and line_rect.height > 0:
                    line_height = line_rect.height
                prev_bottom = line_rect.y1

    doc.close()
    return root


def pretty_xml(root: ET.Element) -> str:
    """Returnerar välformaterad XML-sträng med XML-deklaration."""
    raw     = ET.tostring(root, encoding="unicode")
    reparsed = minidom.parseString(raw.encode("utf-8"))
    pretty   = reparsed.toprettyxml(indent="  ", encoding="utf-8").decode("utf-8")
    # minidom lägger till en extra <?xml?>-rad — ta bort dubbletten
    lines = pretty.split("\n")
    return "\n".join(lines)


# ── Huvudflöde ─────────────────────────────────────────────────────────────────

def convert_pdf(pdf_path: Path, out_path: Path) -> bool:
    """Konverterar en PDF till XML. Returnerar True vid framgång."""
    try:
        log.info(f"  Konverterar {pdf_path.name} → {out_path.name}")
        root    = build_document_xml(pdf_path)
        xml_str = pretty_xml(root)
        out_path.write_text(xml_str, encoding="utf-8")
        size_kb = out_path.stat().st_size // 1024
        log.info(f"  → {out_path.name} ({size_kb} KB)")
        return True
    except Exception as e:
        log.error(f"  FEL vid konvertering av {pdf_path.name}: {e}")
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Konvertera KB PDF → ABBYY FineReader XML")
    parser.add_argument("--file",    type=str, help="Konvertera en specifik PDF-fil")
    parser.add_argument("--dry-run", action="store_true", help="Visa vad som skulle konverteras")
    args = parser.parse_args()

    XML_RAW.mkdir(exist_ok=True)

    if args.file:
        pdfs = [Path(args.file)]
    else:
        pdfs = sorted(PDF_RAW.glob("*.pdf"))

    if not pdfs:
        log.error(f"Inga PDF-filer hittades i {PDF_RAW}")
        return 1

    # Filtrera redan konverterade
    todo     = []
    skipped  = 0
    for pdf in pdfs:
        out = XML_RAW / (pdf.stem + ".xml")
        if out.exists() and out.stat().st_size > 1000:
            skipped += 1
        else:
            todo.append((pdf, out))

    log.info(f"PDF-filer att konvertera: {len(todo)}  (redan klara: {skipped})")

    if args.dry_run:
        for pdf, out in todo:
            log.info(f"  {pdf.name} → {out.name}")
        return 0

    if not todo:
        log.info("Alla PDF:er redan konverterade!")
        return 0

    ok = errors = 0
    for pdf, out in todo:
        if convert_pdf(pdf, out):
            ok += 1
        else:
            errors += 1

    log.info(f"\nKlar! {ok} konverterade, {errors} fel.")
    if ok > 0:
        log.info("XML-filerna ligger i xml_raw/ och kan indexeras av 04_parse_and_index.py")
    return 0 if errors == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
