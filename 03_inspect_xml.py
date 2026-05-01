# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Magnus Kolsjö
# Se LICENSE-filen i repots rot för fullständig licenstext.

"""
03_inspect_xml.py — Analyserar KB:s XML-struktur

Samplar XML-filer från varje stånd och producerar en strukturrapport som
används som ritning för 04_parse_and_index.py och 05_pdf_to_xml.py.

Rapporten sparas som:
  - xml_structure_report.json  — maskinläsbar strukturanalys
  - xml_structure_report.md    — läsbar rapport med exempel och rekommendationer

Användning:
  python3 03_inspect_xml.py              # analysera 2 filer per stånd
  python3 03_inspect_xml.py --samples 5  # analysera 5 filer per stånd
  python3 03_inspect_xml.py --file xml_raw/roa_1627-1632__.xml  # en specifik fil

Krav:
  pip install -r requirements.txt
  xml_raw/ måste innehålla nedladdade XML-filer (kör 02_download_xml.py först)
"""

import json
import argparse
import logging
import random
from pathlib import Path
from collections import Counter, defaultdict
from lxml import etree

OUTPUT_DIR = Path(__file__).parent
XML_RAW    = OUTPUT_DIR / "xml_raw"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ── XML-analys ─────────────────────────────────────────────────────────────────

def analyze_file(xml_path: Path) -> dict:
    """
    Analyserar en enskild XML-fil och returnerar:
    - Rotnod och namnrymd
    - Taghierarki med frekvens
    - Attribut per tagg
    - Textinnehållets fördelning
    - Exempelstycken
    - Total teckenmängd och uppskattad ordmängd
    """
    result = {
        "file":          xml_path.name,
        "size_bytes":    xml_path.stat().st_size,
        "root_tag":      "",
        "namespace":     "",
        "tag_counts":    {},
        "tag_attrs":     {},
        "tag_depths":    {},
        "text_tags":     [],       # taggar som innehåller löptext
        "total_chars":   0,
        "total_words":   0,
        "examples":      {},       # {tagg: [exempel_text, ...]}
        "tree_sample":   "",       # ASCII-träd av strukturen
        "parse_error":   "",
    }

    try:
        tree = etree.parse(str(xml_path))
        root = tree.getroot()
    except etree.XMLSyntaxError as e:
        result["parse_error"] = str(e)
        return result

    # Namnrymd
    ns = ""
    if root.tag.startswith("{"):
        ns = root.tag.split("}")[0][1:]
    result["namespace"] = ns
    result["root_tag"]  = root.tag.split("}")[-1] if "}" in root.tag else root.tag

    # Traversera trädet
    tag_counts  = Counter()
    tag_attrs   = defaultdict(set)
    tag_depths  = defaultdict(list)
    text_chars  = defaultdict(int)
    examples    = defaultdict(list)

    def clean_tag(t):
        return t.split("}")[-1] if "}" in t else t

    def walk(node, depth=0):
        tag = clean_tag(node.tag)
        tag_counts[tag] += 1
        tag_depths[tag].append(depth)

        for attr in node.attrib:
            tag_attrs[tag].add(clean_tag(attr))

        # Text direkt i noden
        text = (node.text or "").strip()
        if text:
            text_chars[tag] += len(text)
            if len(examples[tag]) < 3 and len(text) > 20:
                examples[tag].append(text[:200])

        # Tail-text (text efter avslutande tagg)
        tail = (node.tail or "").strip()
        if tail and len(tail) > 5:
            text_chars[tag + "_tail"] += len(tail)

        for child in node:
            walk(child, depth + 1)

    walk(root)

    # Beräkna statistik
    result["tag_counts"] = dict(tag_counts.most_common())
    result["tag_attrs"]  = {k: sorted(v) for k, v in tag_attrs.items()}
    result["tag_depths"] = {k: {"min": min(v), "max": max(v), "avg": round(sum(v)/len(v), 1)}
                            for k, v in tag_depths.items()}

    # Taggar med mest text = de intressanta för chunking
    text_tags_sorted = sorted(text_chars.items(), key=lambda x: -x[1])
    result["text_tags"]  = [t for t, _ in text_tags_sorted[:10] if not t.endswith("_tail")]
    result["total_chars"] = sum(text_chars.values())
    result["total_words"] = result["total_chars"] // 5  # grov uppskattning
    result["examples"]    = {k: v for k, v in examples.items() if v}

    # ASCII-träd (de 40 vanligaste taggarna i hierarki)
    result["tree_sample"] = build_tree_sample(root, max_depth=6)

    return result


def build_tree_sample(root, max_depth=6) -> str:
    """Bygger ett ASCII-träd av XML-strukturen (unika sökvägar)."""
    seen_paths = set()
    lines = []

    def walk(node, depth, path):
        if depth > max_depth:
            return
        tag = node.tag.split("}")[-1] if "}" in node.tag else node.tag
        current_path = path + "/" + tag
        if current_path not in seen_paths:
            seen_paths.add(current_path)
            attrs = " ".join(f'@{a.split("}")[-1]}' for a in node.attrib)
            attr_str = f"  [{attrs}]" if attrs else ""
            lines.append("  " * depth + f"<{tag}>{attr_str}")
        for child in node:
            walk(child, depth + 1, current_path)

    walk(root, 0, "")
    return "\n".join(lines[:80])  # max 80 rader


# ── Sampling ───────────────────────────────────────────────────────────────────

def pick_samples(volumes_file: Path, samples_per_stand: int) -> list[Path]:
    """Väljer ut XML-filer att analysera, fördelade per stånd."""
    if not volumes_file.exists():
        # Fallback: ta filer direkt från xml_raw/
        all_xml = list(XML_RAW.glob("*.xml"))
        log.warning(f"volumes.json saknas, använder {len(all_xml)} filer från {XML_RAW}")
        return all_xml[:samples_per_stand * 6]

    volumes = json.loads(volumes_file.read_text(encoding="utf-8"))

    by_stand = defaultdict(list)
    for v in volumes:
        if v.get("xml_url") and v.get("volym_id"):
            dest = XML_RAW / f"{v['volym_id']}.xml"
            if dest.exists():
                by_stand[v.get("stand", "okant")].append(dest)

    selected = []
    for stand, files in by_stand.items():
        picked = random.sample(files, min(samples_per_stand, len(files)))
        log.info(f"  {stand:<20} {len(files):>4} filer tillgängliga, väljer {len(picked)}")
        selected.extend(picked)

    return selected


# ── Rapport ────────────────────────────────────────────────────────────────────

def write_markdown_report(analyses: list[dict], out_path: Path) -> None:
    """Skriver en läsbar Markdown-rapport baserat på analyserna."""
    lines = [
        "# XML-strukturrapport — KB:s riksdagstryck",
        "",
        f"Analyserade {len(analyses)} XML-filer.",
        "",
    ]

    # Sammanfattning: gemensamma taggar
    all_tags = Counter()
    all_root_tags = Counter()
    all_ns = Counter()
    for a in analyses:
        all_tags.update(a.get("tag_counts", {}).keys())
        all_root_tags[a["root_tag"]] += 1
        if a["namespace"]:
            all_ns[a["namespace"]] += 1

    lines += [
        "## Rotnod och namnrymd",
        "",
    ]
    for tag, count in all_root_tags.most_common():
        lines.append(f"- `<{tag}>` — {count} filer")
    if all_ns:
        lines.append("")
        for ns, count in all_ns.most_common():
            lines.append(f"- Namnrymd: `{ns}` — {count} filer")
    lines.append("")

    lines += [
        "## Vanligaste taggar (förekommer i flest filer)",
        "",
        "| Tagg | Filer | Snitt per fil |",
        "|---|---|---|",
    ]
    tag_file_count = Counter()
    tag_total = Counter()
    for a in analyses:
        for tag, cnt in a.get("tag_counts", {}).items():
            tag_file_count[tag] += 1
            tag_total[tag] += cnt
    for tag, file_count in tag_file_count.most_common(30):
        avg = tag_total[tag] / file_count
        lines.append(f"| `<{tag}>` | {file_count} | {avg:.0f} |")
    lines.append("")

    lines += [
        "## Taggar med mest textinnehåll (kandidater för chunking)",
        "",
    ]
    text_tag_votes = Counter()
    for a in analyses:
        for tag in a.get("text_tags", [])[:5]:
            text_tag_votes[tag] += 1
    for tag, votes in text_tag_votes.most_common(10):
        lines.append(f"- `<{tag}>` — primär textnod i {votes} av {len(analyses)} filer")
    lines.append("")

    lines += [
        "## Attribut per tagg",
        "",
    ]
    all_attrs = defaultdict(set)
    for a in analyses:
        for tag, attrs in a.get("tag_attrs", {}).items():
            all_attrs[tag].update(attrs)
    for tag in sorted(all_attrs.keys()):
        attrs = sorted(all_attrs[tag])
        if attrs:
            lines.append(f"- `<{tag}>`: {', '.join(f'`@{a}`' for a in attrs)}")
    lines.append("")

    lines += [
        "## Exempeltext per tagg",
        "",
    ]
    example_votes = defaultdict(list)
    for a in analyses:
        for tag, exs in a.get("examples", {}).items():
            example_votes[tag].extend(exs[:1])
    for tag, exs in sorted(example_votes.items()):
        if exs:
            lines.append(f"### `<{tag}>`")
            lines.append(f"```")
            lines.append(exs[0][:300])
            lines.append(f"```")
            lines.append("")

    lines += [
        "## Träd-exempel (en fil per stånd)",
        "",
    ]
    seen_stands = set()
    for a in analyses:
        stand = a["file"].split("_")[0]
        if stand not in seen_stands and a.get("tree_sample"):
            seen_stands.add(stand)
            lines.append(f"### {a['file']}")
            lines.append("```")
            lines.append(a["tree_sample"])
            lines.append("```")
            lines.append("")

    lines += [
        "## Parsfel",
        "",
    ]
    errors = [a for a in analyses if a.get("parse_error")]
    if errors:
        for a in errors:
            lines.append(f"- `{a['file']}`: {a['parse_error']}")
    else:
        lines.append("Inga parsfel.")
    lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    log.info(f"Markdown-rapport sparad till {out_path}")


# ── Huvudflöde ─────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Analysera KB:s XML-struktur")
    parser.add_argument("--samples", type=int, default=2,
                        help="Antal filer att sampla per stånd (standard: 2)")
    parser.add_argument("--file", type=str, default=None,
                        help="Analysera en specifik fil")
    args = parser.parse_args()

    if not XML_RAW.exists() or not any(XML_RAW.glob("*.xml")):
        log.error(f"Inga XML-filer i {XML_RAW} — kör 02_download_xml.py först")
        return 1

    if args.file:
        paths = [Path(args.file)]
        log.info(f"Analyserar en fil: {args.file}")
    else:
        log.info(f"Väljer {args.samples} filer per stånd...")
        paths = pick_samples(OUTPUT_DIR / "volumes.json", args.samples)
        log.info(f"Totalt {len(paths)} filer att analysera")

    if not paths:
        log.error("Inga filer att analysera")
        return 1

    # Analysera
    analyses = []
    for p in paths:
        log.info(f"  Analyserar {p.name}...")
        result = analyze_file(p)
        if result.get("parse_error"):
            log.warning(f"  Parsfel i {p.name}: {result['parse_error']}")
        else:
            log.info(f"  → {result['total_words']:,} ord, {len(result['tag_counts'])} unika taggar, rotnod: <{result['root_tag']}>")
        analyses.append(result)

    # Spara JSON-rapport
    json_path = OUTPUT_DIR / "xml_structure_report.json"
    json_path.write_text(json.dumps(analyses, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(f"JSON-rapport sparad till {json_path}")

    # Spara Markdown-rapport
    md_path = OUTPUT_DIR / "xml_structure_report.md"
    write_markdown_report(analyses, md_path)

    log.info("\nKlar! Läs xml_structure_report.md för en sammanfattning av strukturen.")
    log.info("Rapporten används som underlag för 04_parse_and_index.py och 05_pdf_to_xml.py.")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
