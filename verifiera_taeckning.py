# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Magnus Kolsjö
# Se LICENSE-filen i repots rot för fullständig licenstext.

"""
verifiera_taeckning.py — jämför KB:s deldokuments-manifest mot vad som finns på disk.

Läser manifest_delar.json (skapad av 01_crawl_volumes.py) och kontrollerar för
varje deldokument om dess XML (och för volymer som saknar XML hos KB, dess PDF)
finns nedladdad. Rapporterar exakt vad som saknas och skriver saknade_delar.json
som nedladdningssteget kan utgå från.

Detta är spärren mot tyst trunkering: efter en omkrawl ska nedladdat vara lika
med tillgängligt per deldokument. Allt som inte är det listas här — körningen
avslutas med felkod om något saknas, så att det inte går att råka gå vidare på
ofullständigt underlag.

Skriptet flaggar också filer som finns på disk men inte i manifestet (orphaner
och dubbletter, t.ex. en gammal manuellt nedladdad fil som inte motsvarar något
deldokument hos KB).

Användning:
  python3 verifiera_taeckning.py
"""

import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).parent
MANIFEST = ROOT / "manifest_delar.json"
XML_RAW_DIR = Path(os.getenv("XML_RAW_DIR", ROOT / "xml_raw"))
PDF_RAW_DIR = Path(os.getenv("PDF_RAW_DIR", ROOT / "pdf_raw"))
SAKNADE_FIL = ROOT / "saknade_delar.json"


def pdf_filnamn(pdf_url: str) -> str:
    """Filnamnet 02_download_xml.py sparar PDF:en under (sista ledet i URL:en)."""
    return Path(urlparse(pdf_url).path).name


def main() -> int:
    if not MANIFEST.exists():
        print(f"FEL: {MANIFEST.name} saknas — kör 01_crawl_volumes.py först.")
        return 1

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    saknad_xml = []        # deldokument som har xml_url men vars xml inte finns på disk
    pdf_only_saknad = []   # deldokument utan xml hos KB vars pdf inte finns på disk
    xml_finns = 0
    pdf_only_finns = 0
    xml_stammar = set()    # alla volym_id som ska finnas som xml

    for d in manifest:
        vid = d["volym_id"]
        if d.get("xml_url"):
            xml_stammar.add(vid)
            if (XML_RAW_DIR / f"{vid}.xml").exists():
                xml_finns += 1
            else:
                saknad_xml.append(d)
        elif d.get("pdf_url"):
            # KB har ingen XML för detta deldokument → PDF behålls som källa
            if (PDF_RAW_DIR / pdf_filnamn(d["pdf_url"])).exists():
                pdf_only_finns += 1
            else:
                pdf_only_saknad.append(d)

    # Filer på disk som inte motsvarar något deldokument i manifestet
    pa_disk = {p.stem for p in XML_RAW_DIR.glob("*.xml")} if XML_RAW_DIR.exists() else set()
    orphaner = sorted(pa_disk - xml_stammar)

    antal_xml_forvantat = len(xml_stammar)
    print(f"{'='*60}")
    print("Täckningsverifiering — KB:s deldokument vs disk")
    print(f"{'='*60}")
    print(f"Deldokument i manifest:        {len(manifest)}")
    print(f"  varav XML förväntade:        {antal_xml_forvantat}")
    print(f"  XML på disk:                 {xml_finns}")
    print(f"  XML SAKNAS:                  {len(saknad_xml)}")
    print(f"  PDF-only (ingen XML hos KB): {pdf_only_finns + len(pdf_only_saknad)}")
    print(f"    varav PDF på disk:         {pdf_only_finns}")
    print(f"    PDF-only SAKNAS:           {len(pdf_only_saknad)}")
    print(f"  Filer på disk utan manifest-motsvarighet (orphaner/dubbletter): {len(orphaner)}")
    print(f"{'='*60}")

    if saknad_xml:
        print(f"\nFörsta 20 saknade XML-deldokument:")
        for d in saknad_xml[:20]:
            print(f"  {d['volym_id']:<32} {d.get('period','')}")
    if pdf_only_saknad:
        print(f"\nFörsta 20 saknade PDF-only:")
        for d in pdf_only_saknad[:20]:
            print(f"  {d['volym_id']:<32} {d.get('period','')}")
    if orphaner:
        print(f"\nXML på disk utan KB-motsvarighet ({len(orphaner)} st) — oftast legitima:")
        print(f"  (egna PDF→XML-konverteringar av volymer KB bara har som PDF). Granska att inga är felnedladdat/dubblett:")
        for stam in orphaner[:40]:
            print(f"  {stam}")

    SAKNADE_FIL.write_text(json.dumps({
        "saknad_xml": saknad_xml,
        "pdf_only_saknad": pdf_only_saknad,
        "orphaner": orphaner,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSkrev {SAKNADE_FIL.name} ({len(saknad_xml)} XML + {len(pdf_only_saknad)} PDF-only att hämta).")

    # Felkod om något saknas — gör det omöjligt att omedvetet gå vidare på hål.
    return 0 if not (saknad_xml or pdf_only_saknad) else 2


if __name__ == "__main__":
    sys.exit(main())
