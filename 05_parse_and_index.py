# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Magnus Kolsjö
# Se LICENSE-filen i repots rot för fullständig licenstext.

"""
05_parse_and_index.py — Parsar KB:s XML, genererar embeddings, indexerar i PostgreSQL

Läser alla XML-filer i xml_raw/ (inkl. PDF-konverterade filer från pdf_raw/),
chunkar texten på styckegränser (~600 ord per chunk), genererar embeddings med
KBLab/sentence-bert-swedish-cased och skriver resultatet till PostgreSQL + pgvector.

Krav:
  - PostgreSQL med pgvector-extension (kör docker compose up -d)
  - Konfiguration via .env (se config.example.env)
  - Installerade beroenden: pip install -r requirements.txt

Användning:
  python3 05_parse_and_index.py              # indexera alla filer
  python3 05_parse_and_index.py --reset      # töm databasen och börja om
  python3 05_parse_and_index.py --dry-run    # parsa men skriv inte till databasen
  python3 05_parse_and_index.py --limit 5    # bara 5 volymer (för test)
  python3 05_parse_and_index.py --volym roa_1789_2_  # en specifik volym
  python3 05_parse_and_index.py --force --volym bih_1840-41_7_2  # tvinga omindexering
  python3 05_parse_and_index.py --no-embed   # hoppa över embeddings (snabbt test)

Stöder återupptagning: om körningen avbryts indexeras redan inlästa volymer inte om.
"""

import os
import re
import json
import logging
import argparse
import time
from pathlib import Path

from lxml import etree
from dotenv import load_dotenv
from tqdm import tqdm

load_dotenv()

# ── Konfiguration ────────────────────────────────────────────────────────────

BASE_DIR        = Path(__file__).parent
XML_RAW         = BASE_DIR / os.getenv("XML_RAW_DIR",  "./xml_raw")
PDF_RAW         = BASE_DIR / os.getenv("PDF_RAW_DIR",  "./pdf_raw")
VOLUMES_JSON    = BASE_DIR / "volumes.json"

# KBLab/sentence-bert-swedish-cased är tränad specifikt för semantisk likhet
# (knowledge distillation från all-mpnet-base-v2). Byt inte till
# KBLab/bert-base-swedish-cased — den är en generell MLM-modell och ger sämre sökresultat.
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "KBLab/sentence-bert-swedish-cased")

# Databasanslutning via en enda DATABASE_URL.
# Format: postgresql://anvandare:losenord@localhost:5432/riksdag
DATABASE_URL = os.getenv("DATABASE_URL", "")

CHUNK_TARGET_WORDS = 600      # Målordsantal per chunk
CHUNK_MIN_WORDS    = 50       # Kasta inte bort för korta chunks
EMBED_BATCH_SIZE   = 32       # Antal chunks per embedding-batch
INSERT_BATCH_SIZE  = 128      # Antal chunks per databas-insert

# ABBYY FineReader 10-namnrymd
NS  = "http://www.abbyy.com/FineReader_xml/FineReader10-schema-v1.xml"
NSP = "{" + NS + "}"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ── Stavningsnormalisering ────────────────────────────────────────────────────

# Kompilerade reguljära uttryck för normalisering av äldre svensk stavning.
# Reglerna läses uppifrån och ned — ordningen är kritisk.
#
# Syfte: moderna svenska söktermer (t.ex. "hava", "utan", "efter") ska hitta
# text med historisk stavning (t.ex. "hafwa", "vtan", "effter"). Normalisering
# sker enbart för FTS-indexet (fts_vector) — originaltexten i chunk_text
# bevaras alltid orörd.
_NORM_REGLER: list = [

    # ── Teckennivå ────────────────────────────────────────────────────────────
    # w → v  (w förekommer inte i modern svenska)
    # hafw→hafv (hanteras sedan av hafv-regeln),  wid→vid,  hwad→hvad
    (re.compile(r'w'),                              'v'),

    # hv → v i ordstart  (frågeord och hjälpord)
    # hvad→vad,  hvar→var,  hvem→vem,  hvilken→vilken
    (re.compile(r'\bhv'),                           'v'),

    # v som vokal u i ordstart (v/u-växling), gäller bara före konsonant.
    # vtan→utan,  vppå→uppå,  vthi→uthi (→uti via senare regel).
    # Träffar INTE ord där v följs av vokal: vid, var, vad.
    (re.compile(r'\bv([^aeiouyåäö\s\W])'),          r'u\1'),

    # ── Konsonantgrupper ──────────────────────────────────────────────────────
    # dh → d  (vanlig ändelse och infixmarkering)
    # medh→med,  widh→vid,  godh→god,  blodh→blod
    (re.compile(r'dh'),                             'd'),

    # gh i ordslut → g  (uttalsmarkering utan modern motsvarighet)
    # lagh→lag,  dagh→dag,  nogh→nog
    (re.compile(r'gh\b'),                           'g'),

    # ckh → ck  (stavningsvariant)
    # ickhe→icke
    (re.compile(r'ckh'),                            'ck'),

    # ── Ordstammar ────────────────────────────────────────────────────────────
    # öfv → öv  (prefixet "över-")
    # öfver→över,  öfverste→överste
    (re.compile(r'öfv'),                            'öv'),

    # hafv* → hav*,  hafd* → had*  (hjälpverbet "hava")
    # hafva→hava,  hafver→haver,  hafde→hade
    (re.compile(r'hafv'),                           'hav'),
    (re.compile(r'hafd'),                           'had'),

    # gifv* → giv*  (verbet "giva")
    (re.compile(r'gifv'),                           'giv'),

    # skrifv* → skriv*  (verbet "skriva")
    (re.compile(r'skrifv'),                         'skriv'),

    # lefv* → lev*  (verbet "leva")
    (re.compile(r'lefv'),                           'lev'),

    # blif* → bli*  (verbet "bliva")
    # blifva→bliva,  blifver→bliver
    (re.compile(r'blif'),                           'bli'),

    # ── Prepositioner och partiklar ───────────────────────────────────────────
    # af → av  (preposition, matchar bara som eget ord)
    (re.compile(r'\baf\b'),                         'av'),

    # utaf → utav,  uthi → uti  (komplement till v→u-regeln ovan)
    (re.compile(r'\butaf\b'),                       'utav'),
    (re.compile(r'\buthi\b'),                       'uti'),
    (re.compile(r'\bvthi\b'),                       'uti'),   # säkerhetsnät

    # ── th-stavning (avgränsad lista) ─────────────────────────────────────────
    # Generell \bth→d-regel undviks för att inte fördärva latinska termer
    # (theologia, thesis, theatrum m.fl.) som förekommer i materialet.
    # Listan täcker de statistiskt vanligaste orden i 1500–1700-talsmaterialet.
    (re.compile(r'\bthen\b'),                       'den'),
    (re.compile(r'\bthet\b'),                       'det'),
    (re.compile(r'\bther\b'),                       'der'),
    (re.compile(r'\bthenna\b'),                     'denna'),
    (re.compile(r'\bthenne\b'),                     'denne'),
    (re.compile(r'\bthetta\b'),                     'detta'),
    (re.compile(r'\bthesse\b'),                     'disse'),
    (re.compile(r'\bthessa\b'),                     'dessa'),
    (re.compile(r'\btheras\b'),                     'deras'),
    (re.compile(r'\bdhem\b'),                       'dem'),
    (re.compile(r'\bdher\b'),                       'der'),

    # ── Övriga frekvendsord ───────────────────────────────────────────────────
    (re.compile(r'\bsigh\b'),                       'sig'),
    (re.compile(r'\beffter\b'),                     'efter'),
    (re.compile(r'\bjagh\b'),                       'jag'),
    (re.compile(r'\bnogot\b'),                      'något'),
    (re.compile(r'\bnogon\b'),                      'någon'),
    (re.compile(r'\bnogra\b'),                      'några'),
    (re.compile(r'\briiket\b'),                     'riket'),
    (re.compile(r'\bkonungh\b'),                    'konung'),
    (re.compile(r'\bhoos\b'),                       'hos'),
    (re.compile(r'\bimellan\b'),                    'emellan'),
    (re.compile(r'\bemillan\b'),                    'emellan'),
    (re.compile(r'\bsielff\b'),                     'själv'),
    (re.compile(r'\bsielf\b'),                      'själv'),
    (re.compile(r'\bsädan\b'),                      'sådan'),
    (re.compile(r'\bsädant\b'),                     'sådant'),
    (re.compile(r'\bsampt\b'),                      'samt'),
    (re.compile(r'\bifrå\b'),                       'ifrån'),
    (re.compile(r'\ballenast\b'),                   'allenast'),
]


def normalisera_stavning(text: str) -> str:
    """
    Normaliserar äldre svensk stavning (1521–1866) inför FTS-indexering.

    Originaltexten bevaras alltid orörd i chunk_text — den normaliserade
    versionen används enbart för att bygga fts_vector. Syftet är att moderna
    svenska söktermer ska hitta text med historisk stavning.

    Reglerna är ordnade för att undvika konflikter:
      Teckennivå (w/v/u) → konsonantgrupper (dh, gh, ckh) →
      ordstammar (öfv, hafv …) → prepositioner → th-ord → frekvendsord.

    Lägg till ytterligare regler i _NORM_REGLER ovan — ingen annan kod
    behöver ändras.
    """
    t = text.lower()
    for monster, ersattning in _NORM_REGLER:
        t = monster.sub(ersattning, t)
    return t


# ── Databasschema ─────────────────────────────────────────────────────────────

SCHEMA_STATEMENTS = [
    "CREATE EXTENSION IF NOT EXISTS vector",
    "CREATE EXTENSION IF NOT EXISTS pg_trgm",
    # Schemat kb_riksdagstryck isolerar tabellerna från övriga arbetsströmmar
    # som delar samma PostgreSQL-databas.
    "CREATE SCHEMA IF NOT EXISTS kb_riksdagstryck",
    """
    CREATE TABLE IF NOT EXISTS kb_riksdagstryck.riksdag_chunks (
        id                    BIGSERIAL PRIMARY KEY,
        chunk_text            TEXT        NOT NULL,
        chunk_text_normalized TEXT,
        volym_id              TEXT        NOT NULL,
        titel                 TEXT,
        ar_fran               INTEGER,
        ar_till               INTEGER,
        stand                 TEXT,
        chunk_index           INTEGER,
        xml_url               TEXT,
        pdf_only              BOOLEAN     DEFAULT FALSE,
        char_start            INTEGER,
        char_end              INTEGER,
        web_dok_id            INTEGER,
        embedding             vector(768),
        fts_vector            tsvector GENERATED ALWAYS AS
                              (to_tsvector('swedish',
                                  COALESCE(chunk_text_normalized, chunk_text))) STORED
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS kb_riksdagstryck.indexerade_volymer (
        volym_id      TEXT PRIMARY KEY,
        chunk_antal   INTEGER,
        indexerad_vid    TIMESTAMP DEFAULT NOW()
    )
    """,
]

SCHEMA_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_fts   ON kb_riksdagstryck.riksdag_chunks USING GIN(fts_vector)",
    """CREATE INDEX IF NOT EXISTS idx_vec ON kb_riksdagstryck.riksdag_chunks
        USING hnsw(embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)""",
    "CREATE INDEX IF NOT EXISTS idx_stand ON kb_riksdagstryck.riksdag_chunks (stand)",
    "CREATE INDEX IF NOT EXISTS idx_ar    ON kb_riksdagstryck.riksdag_chunks (ar_fran, ar_till)",
    "CREATE INDEX IF NOT EXISTS idx_trgm  ON kb_riksdagstryck.riksdag_chunks USING GIN(chunk_text gin_trgm_ops)",
]


# ── Databashjälpfunktioner ────────────────────────────────────────────────────

def connect_db():
    """Anslut till PostgreSQL via DATABASE_URL och returnera connection-objekt."""
    import psycopg2
    if not DATABASE_URL:
        raise ValueError(
            "DATABASE_URL är inte satt i .env. "
            "Exempel: postgresql://anvandare:losenord@localhost:5432/riksdag"
        )
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    return conn


def setup_schema(conn):
    """Skapa schema, tabeller och index om de inte finns."""
    with conn.cursor() as cur:
        for stmt in SCHEMA_STATEMENTS:
            cur.execute(stmt)
        conn.commit()
        for stmt in SCHEMA_INDEXES:
            try:
                cur.execute(stmt)
                conn.commit()
            except Exception as exc:
                conn.rollback()
                log.warning("Index-sats misslyckades (ignoreras): %s", exc)
    log.info("Databasschema OK (schema: kb_riksdagstryck)")


def already_indexed(conn, volym_id: str) -> bool:
    """Returnera True om volymen redan är indexerad."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM kb_riksdagstryck.indexerade_volymer WHERE volym_id = %s",
            (volym_id,)
        )
        return cur.fetchone() is not None


def mark_indexed(conn, volym_id: str, chunk_antal: int):
    """Registrera att volymen är klar."""
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO kb_riksdagstryck.indexerade_volymer (volym_id, chunk_antal)
               VALUES (%s, %s)
               ON CONFLICT (volym_id) DO UPDATE
               SET chunk_antal = EXCLUDED.chunk_antal,
                   indexerad_vid  = NOW()""",
            (volym_id, chunk_antal)
        )
    conn.commit()


def reset_database(conn):
    """Töm alla tabeller (används med --reset)."""
    with conn.cursor() as cur:
        cur.execute(
            "TRUNCATE kb_riksdagstryck.riksdag_chunks, "
            "kb_riksdagstryck.indexerade_volymer RESTART IDENTITY CASCADE"
        )
    conn.commit()
    log.warning("Databasen tömd — all data raderad")


def delete_volume(conn, volym_id: str) -> int:
    """Radera alla chunks och indexeringspost för en specifik volym.

    Returnerar antalet raderade chunks.
    """
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM kb_riksdagstryck.riksdag_chunks WHERE volym_id = %s",
            (volym_id,)
        )
        deleted = cur.rowcount
        cur.execute(
            "DELETE FROM kb_riksdagstryck.indexerade_volymer WHERE volym_id = %s",
            (volym_id,)
        )
    conn.commit()
    log.info("Raderade %d befintliga chunks för %s", deleted, volym_id)
    return deleted


def insert_chunks(conn, rows: list):
    """
    Sätt in en batch med chunks.
    Varje rad: (chunk_text, chunk_text_normalized, volym_id, titel,
                ar_fran, ar_till, stand, chunk_index, xml_url, pdf_only, embedding)
    """
    from psycopg2.extras import execute_batch
    sql = """
        INSERT INTO kb_riksdagstryck.riksdag_chunks
            (chunk_text, chunk_text_normalized, volym_id, titel,
             ar_fran, ar_till, stand, chunk_index, xml_url, pdf_only, embedding)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    with conn.cursor() as cur:
        execute_batch(cur, sql, rows, page_size=INSERT_BATCH_SIZE)
    conn.commit()


def db_stats(conn):
    """Skriv ut statistik om databasens innehåll."""
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM kb_riksdagstryck.riksdag_chunks")
        total = cur.fetchone()[0]
        cur.execute(
            "SELECT stand, COUNT(*) FROM kb_riksdagstryck.riksdag_chunks "
            "GROUP BY stand ORDER BY COUNT(*) DESC"
        )
        by_stand = cur.fetchall()
    log.info("Totalt i databasen: %d chunks", total)
    for stand, cnt in by_stand:
        log.info("  %-20s %6d chunks", stand or "(okänt)", cnt)


# ── XML-parsing ───────────────────────────────────────────────────────────────

_SOFT_HYPHEN_RE = re.compile(r"[\u00ac\u00ad]")
_WHITESPACE_RE  = re.compile(r"[ \t]+")


def _clean(text: str) -> str:
    """Ta bort mjuka bindestreck och normalisera blanktecken."""
    text = _SOFT_HYPHEN_RE.sub("", text)
    text = _WHITESPACE_RE.sub(" ", text)
    return text.strip()


def _par_text(par_elem) -> str:
    """Extrahera all text ur ett <par>-element."""
    parts = []
    for fmt in par_elem.iter(NSP + "formatting"):
        if fmt.text:
            parts.append(fmt.text)
    return _clean(" ".join(parts))


def extract_paragraphs(xml_path: Path) -> list:
    """
    Parsa en ABBYY FineReader 10 XML-fil och returnera en lista stycken.
    Itererar sida för sida, block för block. Endast blockType="Text".
    """
    try:
        tree = etree.parse(str(xml_path))
    except etree.XMLSyntaxError as exc:
        log.error("XML-fel i %s: %s", xml_path.name, exc)
        return []

    root = tree.getroot()
    paragraphs = []

    for page in root.iter(NSP + "page"):
        for block in page.iter(NSP + "block"):
            if block.get("blockType") != "Text":
                continue
            for par in block.iter(NSP + "par"):
                text = _par_text(par)
                if text:
                    paragraphs.append(text)

    return paragraphs


# ── Chunkning ──────────────────────────────────────────────────────────────────

PARA_SEP = "\n\n"


def paragraphs_to_chunks(
    paragraphs: list,
    target_words: int = CHUNK_TARGET_WORDS,
    min_words:    int = CHUNK_MIN_WORDS,
) -> list:
    """
    Dela in stycken i chunks om ungefär target_words ord.
    Slutar alltid på en styckegräns.
    Sista chunken slås samman med föregående om den är kortare än min_words.
    """
    chunks  = []
    current = []
    count   = 0

    for par in paragraphs:
        words = len(par.split())
        current.append(par)
        count += words
        if count >= target_words:
            chunks.append(PARA_SEP.join(current))
            current = []
            count   = 0

    if current:
        tail = PARA_SEP.join(current)
        if chunks and len(tail.split()) < min_words:
            chunks[-1] = chunks[-1] + PARA_SEP + tail
        else:
            chunks.append(tail)

    return chunks


# ── Embeddings ─────────────────────────────────────────────────────────────────

_encoder = None


def get_encoder():
    """Ladda SentenceTransformer-modellen (en gång per process).
    Väljer automatiskt MPS (Apple Silicon), CUDA eller CPU.
    """
    global _encoder
    if _encoder is None:
        import torch
        from sentence_transformers import SentenceTransformer
        if torch.backends.mps.is_available():
            device = "mps"
        elif torch.cuda.is_available():
            device = "cuda"
        else:
            device = "cpu"
        log.info("Laddar embedding-modell: %s (device: %s)", EMBEDDING_MODEL, device)
        _encoder = SentenceTransformer(EMBEDDING_MODEL, device=device)
        log.info("Modell laddad")
    return _encoder


def embed_texts(texts: list) -> list:
    """Returnera lista av embeddingvektorer (768 dim, normaliserade)."""
    vecs = get_encoder().encode(
        texts,
        batch_size=EMBED_BATCH_SIZE,
        show_progress_bar=False,
        normalize_embeddings=True,
    )
    return vecs.tolist()


# ── Volymsindex ───────────────────────────────────────────────────────────────

def load_volumes_index() -> dict:
    """Ladda volumes.json och returnera dict { volym_id -> metadata }."""
    with open(VOLUMES_JSON, encoding="utf-8") as f:
        vols = json.load(f)
    return {v["volym_id"]: v for v in vols}


# Prefix → stånd (KB:s eget namnmönster)
_PREFIX_STAND: dict = {
    "roa": "adel",   # Ridderskapet och adeln (äldre beteckning)
    "rda": "adel",   # Ridderskapet och adeln
    "pr":  "praster",
    "bg":  "borgare",
    "bn":  "bonder",
    "bih": "bihang",
    "ku":  "okant",  # Konstitutionsutskottet — tillhör inget enskilt stånd
    "rdbesl": "riksdagsbeslut",
    "reg": "register",
}

def inferera_metadata_fran_volym_id(volym_id: str) -> dict:
    """Gissa stånd och år ur volym_id-prefixet när volymen saknas i volumes.json.

    Returnerar en partiell metadata-dict med de fält som kan härledas säkert.
    Returnerar tom dict om inget prefix matchar.
    """
    # Extrahera prefix (tecken före första underscore eller siffra)
    prefix = re.split(r"[_0-9]", volym_id)[0].lower()
    stand = _PREFIX_STAND.get(prefix)

    # Extrahera år ur filnamnet: t.ex. "1840-41" → 1840/1841, "1765-1766" → 1765/1766
    ar_fran: int | None = None
    ar_till: int | None = None
    m = re.search(r"(\d{4})-(\d{4})", volym_id)
    if m:
        ar_fran = int(m.group(1))
        ar_till = int(m.group(2))
    else:
        m2 = re.search(r"(\d{4})-(\d{2})(?!\d)", volym_id)
        if m2:
            ar_fran = int(m2.group(1))
            # "1840-41" → 1841, "1847-48" → 1848
            century = ar_fran // 100 * 100
            ar_till = century + int(m2.group(2))
        else:
            m3 = re.search(r"(\d{4})", volym_id)
            if m3:
                ar_fran = int(m3.group(1))
                ar_till = ar_fran

    meta: dict = {"volym_id": volym_id}
    if stand:
        meta["stand"] = stand
    if ar_fran:
        meta["ar_fran"] = ar_fran
    if ar_till:
        meta["ar_till"] = ar_till
    return meta


def find_xml_files() -> list:
    """
    Returnera lista av (xml_path, pdf_only) för alla volymer.
    pdf_only=True för filer konverterade från PDF (04_pdf_to_xml.py).
    """
    files = []
    for p in sorted(XML_RAW.glob("*.xml")):
        files.append((p, False))
    if PDF_RAW.exists():
        for p in sorted(PDF_RAW.glob("*.xml")):
            files.append((p, True))
    return files


# ── Per-volym-bearbetning ──────────────────────────────────────────────────────

def process_volume(
    xml_path: Path,
    pdf_only: bool,
    meta:     dict,
    dry_run:  bool,
    no_embed: bool,
    conn,
) -> int:
    """
    Parsa, chunka, normalisera, embedda och indexera en volym.
    Returnerar antal indexerade chunks.
    """
    volym_id   = meta.get("volym_id", xml_path.stem)
    paragraphs = extract_paragraphs(xml_path)

    if not paragraphs:
        log.warning("%s: inga stycken hittades", volym_id)
        return 0

    chunks    = paragraphs_to_chunks(paragraphs)
    avg_words = sum(len(c.split()) for c in chunks) // max(len(chunks), 1)
    log.info(
        "%s: %d stycken -> %d chunks (~%d ord/chunk)",
        volym_id, len(paragraphs), len(chunks), avg_words,
    )

    if dry_run:
        return len(chunks)

    # Normalisera text för FTS — originalet bevaras i chunk_text
    normalized = [normalisera_stavning(c) for c in chunks]

    embeddings = [None] * len(chunks) if no_embed else embed_texts(chunks)

    rows = [
        (
            chunk_text,
            norm_text,
            volym_id,
            meta.get("titel"),
            meta.get("ar_fran"),
            meta.get("ar_till"),
            meta.get("stand"),
            idx,
            meta.get("xml_url"),
            pdf_only,
            embedding,
        )
        for idx, (chunk_text, norm_text, embedding) in enumerate(
            zip(chunks, normalized, embeddings)
        )
    ]

    insert_chunks(conn, rows)
    return len(rows)


# ── main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Parsa och indexera KB:s riksdagstryck i PostgreSQL + pgvector."
    )
    parser.add_argument("--reset",   action="store_true", help="Tom databasen och borja om")
    parser.add_argument("--dry-run", action="store_true", help="Parsa men skriv inte till db")
    parser.add_argument("--limit",   type=int, metavar="N", help="Bara de N forsta volymerna")
    parser.add_argument("--volym",   type=str, metavar="ID", help="En specifik volym")
    parser.add_argument("--force",   action="store_true",
                        help="Tvinga omindexering av --volym (raderar befintliga chunks forst)")
    parser.add_argument("--no-embed",action="store_true", help="Hoppa over embeddings")
    args = parser.parse_args()

    if not VOLUMES_JSON.exists():
        log.error("volumes.json saknas -- kor 01_crawl_volumes.py forst")
        return
    volumes_index = load_volumes_index()
    log.info("%d volymer i volumes.json", len(volumes_index))

    all_files = find_xml_files()
    n_pdf = sum(1 for _, p in all_files if p)
    log.info("%d XML-filer hittade (%d fran pdf_raw/)", len(all_files), n_pdf)

    if args.volym:
        all_files = [(p, po) for p, po in all_files if p.stem == args.volym]
        if not all_files:
            log.error("Hittar ingen XML-fil med volym_id=%s", args.volym)
            return
    if args.limit:
        all_files = all_files[: args.limit]

    if not args.dry_run:
        try:
            conn = connect_db()
            log.info("Ansluten till PostgreSQL via DATABASE_URL")
        except Exception as exc:
            log.error("Kan inte ansluta till PostgreSQL: %s", exc)
            log.error("Kontrollera att Docker-containern kors: docker compose up -d")
            return
        setup_schema(conn)
        if args.reset:
            reset_database(conn)
    else:
        conn = None
        log.info("DRY-RUN aktiverat -- inga andringar skrivs till databasen")

    total_chunks = 0
    skipped      = 0
    errors       = 0
    t0           = time.time()

    for xml_path, pdf_only in tqdm(all_files, unit="vol", desc="Indexerar"):
        vid  = xml_path.stem
        if vid in volumes_index:
            meta = volumes_index[vid]
        else:
            meta = inferera_metadata_fran_volym_id(vid)
            log.warning(
                "%s: saknas i volumes.json — stånd/år infererade från filnamnet "
                "(stand=%s, ar_fran=%s). Lägg till manuellt i volumes.json för korrekt metadata.",
                vid, meta.get("stand"), meta.get("ar_fran")
            )

        if conn and args.force and args.volym and vid == args.volym:
            delete_volume(conn, vid)
        elif conn and not args.reset and already_indexed(conn, vid):
            skipped += 1
            continue

        try:
            n = process_volume(
                xml_path=xml_path,
                pdf_only=pdf_only,
                meta=meta,
                dry_run=args.dry_run,
                no_embed=args.no_embed,
                conn=conn,
            )
            if conn and not args.dry_run:
                mark_indexed(conn, vid, n)
            total_chunks += n

        except Exception as exc:
            log.error("%s: FEL -- %s", vid, exc)
            if conn:
                conn.rollback()
            errors += 1

    elapsed = time.time() - t0
    log.info("=" * 60)
    log.info("Klar! %d chunks indexerade", total_chunks)
    log.info("Hoppade over (redan indexerade): %d volymer", skipped)
    log.info("Fel: %d volymer", errors)
    log.info("Tid: %.1f min (%.1f sek/vol)",
             elapsed / 60, elapsed / max(len(all_files), 1))

    if conn:
        db_stats(conn)
        conn.close()


if __name__ == "__main__":
    main()
