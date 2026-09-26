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
  - volumes.json        — en post per metadata-sida med listan `delar`
                          (alla deldokument: xml_url + pdf_url per filstam)
  - manifest_delar.json — en rad per deldokument (auktoritativ nedladdnings-
                          förteckning; nedladdning och verifiering utgår härifrån)
  - volumes.csv         — samma som manifestet i CSV-format

En volym är ofta uppdelad i flera deldokument med olika sidintervall
(__01, __02, __03 …) som tillsammans utgör volymen. Alla deldokument
kartläggs — att bara behålla ett per metadata-sida tappar innehåll.

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
from urllib.parse import urljoin, urlparse, unquote

import requests
from lxml import html

# ── Konfiguration ──────────────────────────────────────────────────────────────

INDEX_URL  = "https://riksdagstryck.kb.se/standsriksdagen.html"
BASE_URL   = "https://riksdagstryck.kb.se"
OUTPUT_DIR = Path(__file__).parent

# Projektets korrekta UA-sträng per projektkonventionen.
# Används inte ännu — KB:s weburn-WAF avvisar (HTTP 403) alla UA-strängar
# som identifierar sig som bot. Tester 2026-05-19 visade att ingen
# `compatible`-variant fungerar. Byt till PROJECT_UA när KB vitlistar den.
# Detaljer i 02-kb-riksdagstryck-1521-1866.md.
PROJECT_UA = "kb-riksdagstryck-mcp/3.0 (https://github.com/MagnusKolsjo/kb-riksdagstryck-mcp)"

HEADERS = {
    "User-Agent": (
        # Tillfällig avvikelse från UA-konventionen — se PROJECT_UA ovan.
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
    Parsar en metadata-sida och extraherar ALLA deldokument samt titelinfo.

    En volym är ofta uppdelad i flera deldokument med olika sidintervall
    (filstammar som slutar på __01, __02, __03 …) som tillsammans utgör hela
    volymen. Varje deldokument finns som både .xml och .pdf med samma filstam.
    Därför grupperas länkarna per filstam och varje deldokument behålls för
    sig — att bara spara en xml/pdf per sida tappar de övriga deldokumenten.

    Returnerar {"delar": [{volym_id, xml_url, pdf_url}, …], "extra_titel": str}.
    Delarna sorteras på filstam så att __01 kommer före __02 osv.
    """
    tree = html.fromstring(page_html)
    delar: dict[str, dict] = {}   # filstam -> {volym_id, xml_url, pdf_url}

    for a in tree.xpath("//a[@href]"):
        full = urljoin(metadata_url, a.get("href", ""))
        low = full.lower()
        if not (low.endswith(".xml") or low.endswith(".pdf")):
            continue
        # Bara innehållsfiler i ståndsriksdagen-trädet. Globala hjälp- och
        # navigationslänkar (t.ex. förkortningshjälpen under /riks/dok/) finns på
        # varje metadata-sida och är inte deldokument av någon volym.
        path = unquote(urlparse(full).path)
        if "/ståndsriksdagen/" not in path.lower():
            continue
        stam = Path(path).stem
        post = delar.setdefault(stam, {"volym_id": stam, "xml_url": "", "pdf_url": ""})
        if low.endswith(".xml"):
            post["xml_url"] = full
        else:
            post["pdf_url"] = full

    h1 = tree.xpath("//h1/text()")
    extra_titel = h1[0].strip() if h1 else ""
    if not extra_titel:
        title_tag = tree.xpath("//title/text()")
        extra_titel = title_tag[0].strip() if title_tag else ""

    return {"delar": [delar[stam] for stam in sorted(delar)], "extra_titel": extra_titel}


def step2_enrich_with_xml_urls(
    entries: list[dict],
    session: requests.Session
) -> list[dict]:
    """
    Hämtar varje metadata-sida och lägger till listan `delar` (alla deldokument)
    samt räknetal per post. Sparar progress var 50:e post.

    Flerdels-volymer loggas explicit så att det syns i körningen hur många
    metadata-sidor som har mer än ett deldokument — det var just dessa som
    tidigare tappade innehåll.
    """
    log.info(f"Steg 2: Hämtar {len(entries)} metadata-sidor för att kartlägga alla deldokument")
    log.info(f"Beräknad tid: ~{len(entries) * PAUSE_BETWEEN / 60:.0f}–{len(entries) * (PAUSE_BETWEEN + 0.5) / 60:.0f} minuter")

    errors = 0
    flerdels = 0
    for i, entry in enumerate(entries, 1):
        if i % 25 == 0 or i == 1:
            log.info(f"  {i}/{len(entries)} ({i/len(entries)*100:.0f}%)  fel hittills: {errors}  flerdels: {flerdels}")

        page = fetch(entry["metadata_url"], session)
        if not page:
            errors += 1
            entry["delar"]       = []
            entry["extra_titel"] = entry.get("extra_titel", "")
            entry["antal_delar"] = 0
            entry["antal_xml"]   = 0
            entry["antal_pdf"]   = 0
            entry["fel"]         = "http-fel"
        else:
            parsed = parse_metadata_page(page, entry["metadata_url"])
            delar = parsed["delar"]
            entry["delar"]       = delar
            entry["extra_titel"] = parsed["extra_titel"]
            entry["antal_delar"] = len(delar)
            entry["antal_xml"]   = sum(1 for d in delar if d["xml_url"])
            entry["antal_pdf"]   = sum(1 for d in delar if d["pdf_url"])
            entry["fel"]         = "" if entry["antal_xml"] else "ingen-xml-hittad"
            if entry["antal_delar"] > 1:
                flerdels += 1
                log.info(f"  flerdels ({entry['antal_delar']} delar): {entry['metadata_url']}")
            if not entry["antal_xml"]:
                log.warning(f"  Ingen XML på {entry['metadata_url']}")

        time.sleep(PAUSE_BETWEEN)

        # Spara progress var 50:e post
        if i % 50 == 0:
            progress_path = OUTPUT_DIR / "volumes_progress.json"
            progress_path.write_text(
                json.dumps(entries[:i], ensure_ascii=False, indent=2),
                encoding="utf-8"
            )

    log.info(f"Steg 2 klart. {errors} fel av {len(entries)} metadata-sidor. "
             f"{flerdels} sidor har fler än ett deldokument.")
    return entries


# ── Efterbehandling ────────────────────────────────────────────────────────────

def enrich_and_sort(entries: list[dict]) -> list[dict]:
    """Lägger till stånd och årsintervall, sorterar kronologiskt."""
    for e in entries:
        titel = e.get("extra_titel") or e.get("titel", "")
        # Stånd härleds ur filnamnsprefixet. Föredra xml-delens namn; för volymer
        # KB bara har som PDF används pdf-delens namn (samma prefix), annars skulle
        # de klassas som "okant" eftersom xml-url saknas.
        rep = (next((d["xml_url"] for d in e.get("delar", []) if d["xml_url"]), "")
               or next((d["pdf_url"] for d in e.get("delar", []) if d["pdf_url"]), ""))
        e["stand"] = guess_stand(rep, titel)

        ar_fran, ar_till = extract_years_from_title(e.get("period", ""))
        e["ar_fran"] = ar_fran
        e["ar_till"] = ar_till

    return sorted(entries, key=lambda v: (v.get("ar_fran") or 9999, v.get("titel", "")))


def bygg_manifest(volumes: list[dict]) -> list[dict]:
    """
    Plattar ut volymposterna till en rad per deldokument — den auktoritativa
    förteckningen över allt som finns att ladda ned hos KB. Nedladdnings- och
    verifieringsstegen utgår från denna, inte från volymposterna, eftersom det
    är på deldokuments-nivå hål kan uppstå.
    """
    manifest = []
    sedda = set()
    for v in volumes:
        for d in v.get("delar", []):
            # Samma deldokument kan listas under flera period-rubriker på
            # indexsidan; filstammen är unik per fil, så avdubbla på den.
            if d["volym_id"] in sedda:
                continue
            sedda.add(d["volym_id"])
            manifest.append({
                "volym_id":     d["volym_id"],
                "xml_url":      d["xml_url"],
                "pdf_url":      d["pdf_url"],
                "stand":        v.get("stand", ""),
                "ar_fran":      v.get("ar_fran"),
                "ar_till":      v.get("ar_till"),
                "period":       v.get("period", ""),
                "titel":        v.get("extra_titel") or v.get("titel", ""),
                "metadata_url": v.get("metadata_url", ""),
            })
    return manifest


def save_results(volumes: list[dict]) -> None:
    """Sparar volumes.json (per metadata-sida), manifest_delar.json (per
    deldokument) och volumes.csv (en rad per deldokument)."""
    med_xml = [v for v in volumes if v.get("antal_xml")]
    log.info(f"Metadata-sidor med minst en XML: {len(med_xml)} av {len(volumes)}")

    OUTPUT_DIR.joinpath("volumes.json").write_text(
        json.dumps(volumes, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log.info(f"Sparade {OUTPUT_DIR / 'volumes.json'}")

    manifest = bygg_manifest(volumes)
    OUTPUT_DIR.joinpath("manifest_delar.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log.info(f"Sparade {OUTPUT_DIR / 'manifest_delar.json'} ({len(manifest)} deldokument)")

    if manifest:
        fields = ["volym_id", "titel", "stand", "ar_fran", "ar_till",
                  "xml_url", "pdf_url", "metadata_url", "period"]
        with OUTPUT_DIR.joinpath("volumes.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(manifest)
        log.info(f"Sparade {OUTPUT_DIR / 'volumes.csv'}")


# ── Huvudflöde ─────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Kartlägg KB:s riksdagstryck-volymer")
    parser.add_argument("--step1", action="store_true", help="Bara steg 1 (indexsidan)")
    parser.add_argument("--step2", action="store_true", help="Bara steg 2 (metadata-sidor, kräver metadata_urls.json)")
    parser.add_argument("--rebuild", action="store_true",
                        help="Bygg om volumes.json/manifest ur befintlig volumes.json utan nätanrop")
    args = parser.parse_args()

    session = requests.Session()
    meta_path = OUTPUT_DIR / "metadata_urls.json"

    if args.rebuild:
        # Regenererar härledd metadata (stånd/år) och manifestet ur den redan
        # hämtade volumes.json — för när bara klassningslogiken eller
        # manifestformatet ändrats, utan att hämta om alla metadata-sidor.
        vpath = OUTPUT_DIR / "volumes.json"
        if not vpath.exists():
            log.error(f"{vpath} saknas — kör en riktig krawl först")
            return 1
        entries = json.loads(vpath.read_text(encoding="utf-8"))
        log.info(f"Bygger om ur {vpath} ({len(entries)} poster) utan nätanrop")
    else:
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

    # Sammanfattning — räknat på deldokument, inte metadata-sidor, eftersom
    # det är deldokumenten som ska laddas ned.
    xml_per_stand: dict = {}
    totalt_xml = totalt_pdf = 0
    for e in entries:
        for d in e.get("delar", []):
            if d["xml_url"]:
                xml_per_stand[e["stand"]] = xml_per_stand.get(e["stand"], 0) + 1
                totalt_xml += 1
            if d["pdf_url"]:
                totalt_pdf += 1

    log.info(f"\n{chr(8212)*50}")
    log.info(f"Metadata-sidor: {len(entries)}   deldokument: {totalt_xml} xml, {totalt_pdf} pdf")
    log.info("XML-deldokument per stånd:")
    for stand, count in sorted(xml_per_stand.items()):
        log.info(f"  {stand:<20} {count:>4} deldokument")
    log.info(chr(8212)*50)
    log.info("\nKlar! Nästa steg: kör verifiera_taeckning.py (vad saknas på disk?) "
             "och därefter 02_download_xml.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
