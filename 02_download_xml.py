# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Magnus Kolsjö
# Se LICENSE-filen i repots rot för fullständig licenstext.

"""
02_download_xml.py — Nedladdning av XML- och PDF-filer från KB

Läser volumes.json (skapad av 01_crawl_volumes.py) och laddar ner:
  - XML-filer  → xml_raw/{volym_id}.xml
  - PDF-filer  → pdf_raw/{volym_id}.pdf  (för de 16 PDF-only volymerna)

Stöd för att fortsätta avbruten nedladdning — redan nedladdade filer hoppas över.
Sparar en loggfil (download_log.json) med status per volym.

OBS: weburn.kb.se kräver korrekt User-Agent och Referer-header — se teknisk åtkomst i README.

Användning:
  python3 02_download_xml.py              # ladda ner allt
  python3 02_download_xml.py --xml-only   # bara XML
  python3 02_download_xml.py --pdf-only   # bara PDF
  python3 02_download_xml.py --dry-run    # visa vad som skulle laddas ner

Krav:
  pip install -r requirements.txt
  Kopiera config.example.env till .env och justera vid behov.
"""

import json
import time
import logging
import argparse
import hashlib
from pathlib import Path
from dotenv import load_dotenv
import os

import requests
from tqdm import tqdm

load_dotenv()

# ── Konfiguration ──────────────────────────────────────────────────────────────

XML_RAW_DIR    = Path(os.getenv("XML_RAW_DIR", "./xml_raw"))
PDF_RAW_DIR    = Path(os.getenv("PDF_RAW_DIR", "./pdf_raw"))
DOWNLOAD_DELAY = float(os.getenv("DOWNLOAD_DELAY", "0.5"))
MAX_ERRORS     = int(os.getenv("MAX_ERRORS", "10"))
OUTPUT_DIR     = Path(__file__).parent
VOLUMES_FILE   = OUTPUT_DIR / "volumes.json"
LOG_FILE       = OUTPUT_DIR / "download_log.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://riksdagstryck.kb.se/standsriksdagen.html",
    "Accept-Language": "sv-SE,sv;q=0.9,en;q=0.8",
}

REQUEST_TIMEOUT = 60   # sekunder — PDF-filer kan vara stora

# ── Logging ────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ── Hjälpfunktioner ────────────────────────────────────────────────────────────

def load_log() -> dict:
    """Läser in nedladdningsloggen om den finns."""
    if LOG_FILE.exists():
        return json.loads(LOG_FILE.read_text(encoding="utf-8"))
    return {}


def save_log(download_log: dict) -> None:
    LOG_FILE.write_text(json.dumps(download_log, ensure_ascii=False, indent=2), encoding="utf-8")


def file_is_valid(path: Path, min_bytes: int = 1000) -> bool:
    """Kontrollerar att en fil existerar och inte är suspekt liten."""
    return path.exists() and path.stat().st_size >= min_bytes


def download_file(url: str, dest: Path, session: requests.Session) -> tuple[bool, str]:
    """
    Laddar ner en fil till dest.
    Returnerar (success, felmeddelande eller "").
    Använder streaming för att hantera stora filer utan att ladda allt i minnet.
    """
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".tmp")

        with session.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT, stream=True) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length", 0))
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)

        # Kontrollera att filen inte är suspekt liten
        size = tmp.stat().st_size
        if size < 500:
            tmp.unlink()
            return False, f"Fil för liten ({size} bytes) — troligen felsvar från server"

        tmp.rename(dest)
        return True, ""

    except requests.RequestException as e:
        if tmp.exists():
            tmp.unlink()
        return False, str(e)


def format_size(n_bytes: int) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if n_bytes < 1024:
            return f"{n_bytes:.1f} {unit}"
        n_bytes /= 1024
    return f"{n_bytes:.1f} TB"


# ── Huvudflöde ─────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Ladda ner KB:s riksdagstryck")
    parser.add_argument("--xml-only", action="store_true", help="Bara XML-filer")
    parser.add_argument("--pdf-only", action="store_true", help="Bara PDF-filer")
    parser.add_argument("--dry-run",  action="store_true", help="Visa vad som skulle laddas ner")
    args = parser.parse_args()

    if not VOLUMES_FILE.exists():
        log.error(f"{VOLUMES_FILE} saknas — kör 01_crawl_volumes.py först")
        return 1

    volumes = json.loads(VOLUMES_FILE.read_text(encoding="utf-8"))
    download_log = load_log()

    # Bygg nedladdningskö
    queue = []

    if not args.pdf_only:
        xml_vols = [v for v in volumes if v.get("xml_url") and v.get("volym_id")]
        for v in xml_vols:
            dest = XML_RAW_DIR / f"{v['volym_id']}.xml"
            queue.append({
                "volym_id": v["volym_id"],
                "url":      v["xml_url"],
                "dest":     dest,
                "typ":      "xml",
                "stand":    v.get("stand", ""),
                "period":   v.get("period", ""),
                "titel":    (v.get("extra_titel") or v.get("titel", ""))[:60],
            })

    if not args.xml_only:
        pdf_vols = [v for v in volumes if not v.get("xml_url") and v.get("pdf_url")]
        for v in pdf_vols:
            fname = v["pdf_url"].split("/")[-1]
            stem  = fname.replace(".pdf", "")
            dest  = PDF_RAW_DIR / fname
            queue.append({
                "volym_id": stem,
                "url":      v["pdf_url"],
                "dest":     dest,
                "typ":      "pdf",
                "stand":    v.get("stand", ""),
                "period":   v.get("period", ""),
                "titel":    (v.get("extra_titel") or v.get("titel", ""))[:60],
            })

    # Filtrera redan nedladdade
    todo    = [q for q in queue if not file_is_valid(q["dest"])]
    skipped = len(queue) - len(todo)

    log.info(f"Totalt i kö:        {len(queue):>5}  ({len([q for q in queue if q['typ']=='xml'])} XML, {len([q for q in queue if q['typ']=='pdf'])} PDF)")
    log.info(f"Redan nedladdade:   {skipped:>5}  (hoppas över)")
    log.info(f"Att ladda ner:      {len(todo):>5}")

    if args.dry_run:
        log.info("\n-- DRY RUN -- Första 20 i kön:")
        for item in todo[:20]:
            log.info(f"  [{item['typ'].upper()}] {item['volym_id']}  ({item['period']})")
        return 0

    if not todo:
        log.info("Allt redan nedladdat!")
        return 0

    XML_RAW_DIR.mkdir(exist_ok=True)
    PDF_RAW_DIR.mkdir(exist_ok=True)

    session  = requests.Session()
    errors   = 0
    ok_count = 0
    total_bytes = 0

    log.info(f"\nStartar nedladdning med {DOWNLOAD_DELAY}s paus mellan anrop...")

    for item in tqdm(todo, unit="fil", ncols=80):
        vid = item["volym_id"]

        success, errmsg = download_file(item["url"], item["dest"], session)

        if success:
            size = item["dest"].stat().st_size
            total_bytes += size
            ok_count += 1
            download_log[vid] = {
                "status": "ok",
                "typ":    item["typ"],
                "dest":   str(item["dest"]),
                "bytes":  size,
                "url":    item["url"],
            }
        else:
            errors += 1
            log.warning(f"FEL [{vid}]: {errmsg}")
            download_log[vid] = {
                "status": "fel",
                "typ":    item["typ"],
                "url":    item["url"],
                "fel":    errmsg,
            }
            if errors >= MAX_ERRORS:
                log.error(f"Avbryter efter {MAX_ERRORS} fel i rad")
                save_log(download_log)
                return 1

        save_log(download_log)
        time.sleep(DOWNLOAD_DELAY)

    # Sammanfattning
    log.info(f"\n{'─'*50}")
    log.info(f"Nedladdning klar!")
    log.info(f"  Lyckades:   {ok_count}")
    log.info(f"  Fel:        {errors}")
    log.info(f"  Totalt:     {format_size(total_bytes)}")
    log.info(f"  XML-filer:  {XML_RAW_DIR}")
    log.info(f"  PDF-filer:  {PDF_RAW_DIR}")
    log.info(f"{'─'*50}")
    log.info("\nNästa steg: kör 03_inspect_xml.py för att undersöka XML-strukturen")
    return 0 if errors == 0 else 2


if __name__ == "__main__":
    import sys
    sys.exit(main())
