# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Magnus Kolsjö
# Se LICENSE-filen i repots rot för fullständig licenstext.

"""
01_crawl_volumes.py — Kartläggning av KB:s riksdagstryck-volymer

Strukturen på KB:s sajt är tvåstegs:
  1. Indexsidan (riksdagstryck.kb.se) → lista med metadata-URL:er per volym
  2. Varje metadata-sida (weburn.kb.se/riks/metadata/...) → XML- och PDF-länk

Skriptet hämtar båda stegen och sparar:
  - metadata_urls.json  — alla metadata-URL:er från indexsidan (steg 1)
  - volumes.json        — fullständig volymlista med XML-URL:er (steg 1+2)
  - volumes.csv         — samma data i CSV-format

Mellanresultatet (metadata_urls.json) sparas efter steg 1 så att
steg 2 kan köras om separat om något avbryts.

OBS: weburn.kb.se kräver korrekt User-Agent och Referer-header — se teknisk åtkomst i README.

Användning:
  python3 01_crawl_volumes.py            # kör hela flödet
  python3 01_crawl_volumes.py --step1    # bara indexsidan
  python3 01_crawl_volumes.py --step2    # bara metadata-sidorna (kräver metadata_urls.json)

Krav:
  pip install -r requirements.txt
"""

import json
import csv
import sys
import re
import time
import logging
import argparse
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from lxml import html

# ── Konfiguration ──────────────────────────────────────────────────────────────

INDEX_URL  = "https://riksdagstryck.kb.se/standsriksdagen.html"
BASE_URL   = "https://riksdagstryck.kb.se"
OUTPUT_DIR = Path(__file__).parent

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://riksdagstryck.kb.se/standsriksdagen.html",
    "Accept-Language": "sv-SE,sv;q=0.9,en;q=0.8",
}

REQUEST_TIMEOUT  = 30   # sekunder
PAUSE_BETWEEN    = 0.3  # sekunder mellan metadata-anrop

# ── Logging ────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ── Hjälpfunktioner ────────────────────────────────────────────────────────────

def fetch(url: str, session: requests.Session) -> str | None:
    try:
        r = session.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        r.encoding = r.apparent_encoding or "utf-8"
        return r.text
    except requests.RequestException as e:
        log.error(f"Fel vid hämtning av {url}: {e}")
        return None


def guess_stand(xml_url: str, titel: str) -> str:
    """
    Klassificerar stånd baserat på XML-filnamnsprefix (primärt)
    med titeln som fallback för bihang och okända.
    Kända prefix: roa/rda=adel, pr=praster, bn=bonder,
                  bg=borgare, rdbesl=riksdagsbeslut,
                  bih=bihang (bilagor), ku=meta, sakreg/persreg=register
    """
    from pathlib import Path as _Path
    from urllib.parse import urlparse as _urlparse
    fname = _Path(_urlparse(xml_url).path).name.lower() if xml_url else ""

    if fname.startswith(("roa_", "rda_")):
        return "adel"
    if fname.startswith("pr_"):
        return "praster"
    if fname.startswith("bn_"):
        return "bonder"
    if fname.startswith(("bg_", "borgarprotokoll")):
        return "borgare"
    if fname.startswith("rdbesl_"):
        return "riksdagsbeslut"
    if fname.startswith(("sakreg_", "persreg_")):
        return "register"
    if fname.startswith("ku_"):
        return "okant"

    # Fallback för bihang och övriga: titel
    t = titel.lower()
    if any(w in t for w in ["ridderskapet", "adels", "adelns"]):
        return "adel"
    if any(w in t for w in ["prästestånd", "prästeståndet"]):
        return "praster"
    if any(w in t for w in ["borgarstånd", "borgarståndet"]):
        return "borgare"
    if any(w in t for w in ["bondestånd", "bondeståndet"]):
        return "bonder"
    if any(w in t for w in ["riksdagsbeslut"]):
        return "riksdagsbeslut"
    if fname.startswith("bih_"):
        return "bihang"
    return "okant"


def extract_years_from_title(titel: str) -> tuple[int | None, int | None]:
    """Extraherar årsintervall ur titeln, t.ex. '1627-1632' → (1627, 1632)."""
    m = re.search(r"(\d{4})[-–](\d{4})", titel)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"(\d{4})", titel)
    if m:
        yr = int(m.group(1))
        return yr, yr
    return None, None


# ── Steg 1: Extrahera metadata-URL:er från indexsidan ─────────────────────────

def step1_get_metadata_urls(session: requests.Session) -> list[dict]:
    """
    Hämtar indexsidan och extraherar alla metadata-URL:er (weburn.kb.se/riks/metadata/...).
    Returnerar lista med dicts: {metadata_url, titel, ar_label}
    """
    log.info(f"Steg 1: Hämtar indexsida {INDEX_URL}")
    page = fetch(INDEX_URL, session)
    if not page:
        log.error("Kunde inte hämta indexsidan.")
        return []

    log.info(f"Indexsida hämtad ({len(page):,} tecken)")
    tree  = html.fromstring(page)
    links = tree.xpath("//a[@href]")
    log.info(f"Totalt {len(links)} länkar på indexsidan")

    entries = []
    seen    = set()
    current_period = ""

    for a in links:
        href  = a.get("href", "").strip()
        text  = (a.text_content() or "").strip()
        full  = urljoin(BASE_URL, href)

        # Periodrubriker är ankarlänkar som #collapse1521-1560
        if href.startswith("#collapse"):
            current_period = href.replace("#collapse", "")
            continue

        if "weburn.kb.se/riks/metadata/" in full and full not in seen:
            seen.add(full)
            entries.append({
                "metadata_url": full,
                "titel":        text,
                "period":       current_period,
            })

    log.info(f"Hittade {len(entries)} metadata-URL:er")
    return entries


# ── Steg 2: Hämta XML-URL från varje metadata-sida ────────────────────────────

def parse_metadata_page(page_html: str, metadata_url: str) -> dict:
    """
    Parsar en metadata-sida och extraherar XML- och PDF-URL:er
    samt eventuell ytterligare titelinformation.
    """
    tree = html.fromstring(page_html)
    result = {"xml_url": "", "pdf_url": "", "volym_id": "", "extra_titel": ""}

    # Sök efter alla href:ar
    for a in tree.xpath("//a[@href]"):
        href = a.get("href", "")
        full = urljoin(metadata_url, href)

        if full.lower().endswith(".xml"):
            result["xml_url"] = full
            result["volym_id"] = Path(urlparse(full).path).stem

        elif full.lower().endswith(".pdf"):
            if not result["pdf_url"]:   # ta bara första PDF:en
                result["pdf_url"] = full

    # Försök hämta titel från <h1> eller <title>
    h1 = tree.xpath("//h1/text()")
    if h1:
        result["extra_titel"] = h1[0].strip()
    else:
        title_tag = tree.xpath("//title/text()")
        if title_tag:
            result["extra_titel"] = title_tag[0].strip()

    return result


def step2_enrich_with_xml_urls(
    entries: list[dict],
    session: requests.Session
) -> list[dict]:
    """
    Hämtar varje metadata-sida och lägger till xml_url, pdf_url och volym_id.
    Sparar progress var 50:e post.
    """
    log.info(f"Steg 2: Hämtar {len(entries)} metadata-sidor för att hitta XML-URL:er")
    log.info(f"Beräknad tid: ~{len(entries) * PAUSE_BETWEEN / 60:.0f}–{len(entries) * (PAUSE_BETWEEN + 0.5) / 60:.0f} minuter")

    errors = 0
    for i, entry in enumerate(entries, 1):
        if i % 25 == 0 or i == 1:
            log.info(f"  {i}/{len(entries)} ({i/len(entries)*100:.0f}%)  fel hittills: {errors}")

        page = fetch(entry["metadata_url"], session)
        if not page:
            errors += 1
            entry["xml_url"]  = ""
            entry["pdf_url"]  = ""
            entry["volym_id"] = ""
            entry["fel"]      = "http-fel"
        else:
            parsed = parse_metadata_page(page, entry["metadata_url"])
            entry.update(parsed)
            entry["fel"] = "" if parsed["xml_url"] else "ingen-xml-hittad"
            if not parsed["xml_url"]:
                log.warning(f"  Ingen XML-URL på {entry['metadata_url']}")

        time.sleep(PAUSE_BETWEEN)

        # Spara progress var 50:e post
        if i % 50 == 0:
            progress_path = OUTPUT_DIR / "volumes_progress.json"
            progress_path.write_text(
                json.dumps(entries[:i], ensure_ascii=False, indent=2),
                encoding="utf-8"
            )

    log.info(f"Steg 2 klart. {errors} fel av {len(entries)} metadata-sidor.")
    return entries


# ── Efterbehandling ────────────────────────────────────────────────────────────

def enrich_and_sort(entries: list[dict]) -> list[dict]:
    """Lägger till stånd och årsintervall, sorterar kronologiskt."""
    for e in entries:
        titel = e.get("extra_titel") or e.get("titel", "")
        e["stand"] = guess_stand(e.get("xml_url", ""), titel)

        ar_fran, ar_till = extract_years_from_title(e.get("period", ""))
        e["ar_fran"] = ar_fran
        e["ar_till"] = ar_till

    return sorted(entries, key=lambda v: (v.get("ar_fran") or 9999, v.get("titel", "")))


def save_results(volumes: list[dict]) -> None:
    """Sparar volumes.json och volumes.csv."""
    # Filtrera bara poster med XML-URL för CSV:en
    with_xml = [v for v in volumes if v.get("xml_url")]
    log.info(f"Volymer med XML-URL: {len(with_xml)} av {len(volumes)}")

    OUTPUT_DIR.joinpath("volumes.json").write_text(
        json.dumps(volumes, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log.info(f"Sparade {OUTPUT_DIR / 'volumes.json'}")

    if volumes:
        fields = ["volym_id", "titel", "stand", "ar_fran", "ar_till",
                  "xml_url", "pdf_url", "metadata_url", "period", "fel"]
        with OUTPUT_DIR.joinpath("volumes.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(volumes)
        log.info(f"Sparade {OUTPUT_DIR / 'volumes.csv'}")


# ── Huvudflöde ─────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Kartlägg KB:s riksdagstryck-volymer")
    parser.add_argument("--step1", action="store_true", help="Bara steg 1 (indexsidan)")
    parser.add_argument("--step2", action="store_true", help="Bara steg 2 (metadata-sidor, kräver metadata_urls.json)")
    args = parser.parse_args()

    session = requests.Session()
    meta_path = OUTPUT_DIR / "metadata_urls.json"

    # Steg 1
    if not args.step2:
        entries = step1_get_metadata_urls(session)
        if not entries:
            return 1
        meta_path.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info(f"Metadata-URL:er sparade till {meta_path}")

        if args.step1:
            log.info("Steg 1 klart. Kör med --step2 (eller utan flagga) för att hämta XML-URL:er.")
            return 0
    else:
        if not meta_path.exists():
            log.error(f"{meta_path} saknas — kör först utan --step2 eller med --step1")
            return 1
        entries = json.loads(meta_path.read_text(encoding="utf-8"))
        log.info(f"Läste in {len(entries)} metadata-URL:er från {meta_path}")

    # Steg 2
    entries = step2_enrich_with_xml_urls(entries, session)
    entries = enrich_and_sort(entries)
    save_results(entries)

    # Sammanfattning
    stand_count: dict = {}
    for e in entries:
        if e.get("xml_url"):
            stand_count[e["stand"]] = stand_count.get(e["stand"], 0) + 1

    log.info(f"\n{chr(8212)*50}")
    log.info("Volymer med XML-URL per stånd:")
    for stand, count in sorted(stand_count.items()):
        log.info(f"  {stand:<20} {count:>4} volymer")
    log.info(chr(8212)*50)
    log.info("\nKlar! Nästa steg: kör 02_download_xml.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
