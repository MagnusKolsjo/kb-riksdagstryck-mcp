# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Magnus Kolsjö
# Se LICENSE-filen i repots rot för fullständig licenstext.

"""
04_parse_and_index.py — Parsar KB:s XML, genererar embeddings, indexerar i PostgreSQL

Läser alla XML-filer i xml_raw/ (inkl. PDF-konverterade filer från pdf_raw/),
chunkar texten på styckegränser (~600 ord per chunk), genererar embeddings med
KBLab/bert-base-swedish-cased och skriver resultatet till PostgreSQL + pgvector.

Krav:
  - PostgreSQL med pgvector-extension (kör docker compose up -d)
  - Konfiguration via .env (se config.example.env)
  - Installerade beroenden: pip install -r requirements.txt

Användning:
  python3 04_parse_and_index.py              # indexera alla filer
  python3 04_parse_and_index.py --reset      # töm databasen och börja om
  python3 04_parse_and_index.py --dry-run    # parsa men skriv inte till databasen
  python3 04_parse_and_index.py --limit 5    # bara 5 volymer (för test)
  python3 04_parse_and_index.py --volym roa_1789_2_  # en specifik volym
  python3 04_parse_and_index.py --no-embed   # hoppa över embeddings (snabbt test)

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

PGHOST     = os.getenv("PGHOST",     "localhost")
PGPORT     = int(os.getenv("PGPORT", "5432"))
PGDATABASE = os.getenv("PGDATABASE", "riksdagstryck")
PGUSER     = os.getenv("PGUSER",     "riksdag")
PGPASSWORD = os.getenv("PGPASSWORD", "")

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

# ── Databasschema ─────────────────────────────────────────────────────────────

SCHEMA_STATEMENTS = [
    "CREATE EXTENSION IF NOT EXISTS vector",
    "CREATE EXTENSION IF NOT EXISTS pg_trgm",
    """
    CREATE TABLE IF NOT EXISTS riksdag_chunks (
        id            BIGSERIAL PRIMARY KEY,
        chunk_text    TEXT        NOT NULL,
        volym_id      TEXT        NOT NULL,
        titel         TEXT,
        ar_fran       INTEGER,
        ar_till       INTEGER,
        stand         TEXT,
        chunk_index   INTEGER,
        xml_url       TEXT,
        pdf_only      BOOLEAN     DEFAULT FALSE,
        embedding     vector(768)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS indexed_volumes (
        volym_id      TEXT PRIMARY KEY,
        chunk_count   INTEGER,
        indexed_at    TIMESTAMP DEFAULT NOW()
    )
    """,
]

SCHEMA_INDEXES = [
    """
    DO $body$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'riksdag_chunks'
              AND column_name = 'fts_vector'
        ) THEN
            ALTER TABLE riksdag_chunks
                ADD COLUMN fts_vector tsvector
                GENERATED ALWAYS AS (to_tsvector('swedish', chunk_text)) STORED;
        END IF;
    END
    $body$
    """,
    "CREATE INDEX IF NOT EXISTS idx_fts   ON riksdag_chunks USING GIN(fts_vector)",
    """CREATE INDEX IF NOT EXISTS idx_vec ON riksdag_chunks
        USING hnsw(embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)""",
    "CREATE INDEX IF NOT EXISTS idx_stand ON riksdag_chunks (stand)",
    "CREATE INDEX IF NOT EXISTS idx_ar    ON riksdag_chunks (ar_fran, ar_till)",
    "CREATE INDEX IF NOT EXISTS idx_trgm  ON riksdag_chunks USING GIN(chunk_text gin_trgm_ops)",
]


# ── Databashjälpfunktioner ────────────────────────────────────────────────────

def connect_db():
    """Anslut till PostgreSQL och returnera connection-objekt."""
    import psycopg2
    conn = psycopg2.connect(
        host=PGHOST, port=PGPORT,
        dbname=PGDATABASE, user=PGUSER, password=PGPASSWORD
    )
    conn.autocommit = False
    return conn


def setup_schema(conn):
    """Skapa tabeller och index om de inte finns."""
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
    log.info("Databasschema OK")


def already_indexed(conn, volym_id: str) -> bool:
    """Returnera True om volymen redan är indexerad."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM indexed_volumes WHERE volym_id = %s",
            (volym_id,)
        )
        return cur.fetchone() is not None


def mark_indexed(conn, volym_id: str, chunk_count: int):
    """Registrera att volymen är klar."""
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO indexed_volumes (volym_id, chunk_count)
               VALUES (%s, %s)
               ON CONFLICT (volym_id) DO UPDATE
               SET chunk_count = EXCLUDED.chunk_count,
                   indexed_at  = NOW()""",
            (volym_id, chunk_count)
        )
    conn.commit()


def reset_database(conn):
    """Töm alla tabeller (används med --reset)."""
    with conn.cursor() as cur:
        cur.execute(
            "TRUNCATE riksdag_chunks, indexed_volumes RESTART IDENTITY CASCADE"
        )
    conn.commit()
    log.warning("Databasen tömd — all data raderad")


def insert_chunks(conn, rows: list):
    """
    Sätt in en batch med chunks.
    Varje rad: (chunk_text, volym_id, titel, ar_fran, ar_till,
                stand, chunk_index, xml_url, pdf_only, embedding)
    """
    from psycopg2.extras import execute_batch
    sql = """
        INSERT INTO riksdag_chunks
            (chunk_text, volym_id, titel, ar_fran, ar_till,
             stand, chunk_index, xml_url, pdf_only, embedding)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    with conn.cursor() as cur:
        execute_batch(cur, sql, rows, page_size=INSERT_BATCH_SIZE)
    conn.commit()


def db_stats(conn):
    """Skriv ut statistik om databasens innehåll."""
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM riksdag_chunks")
        total = cur.fetchone()[0]
        cur.execute(
            "SELECT stand, COUNT(*) FROM riksdag_chunks "
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


def find_xml_files() -> list:
    """
    Returnera lista av (xml_path, pdf_only) för alla volymer.
    pdf_only=True för filer konverterade från PDF (05_pdf_to_xml.py).
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
    Parsa, chunka, embedda och indexera en volym.
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

    embeddings = [None] * len(chunks) if no_embed else embed_texts(chunks)

    rows = [
        (
            chunk_text,
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
        for idx, (chunk_text, embedding) in enumerate(zip(chunks, embeddings))
    ]

    insert_chunks(conn, rows)
    return len(rows)


# ── main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Parsa och indexera KB:s riksdagstryck i PostgreSQL + pgvector."
    )
    parser.add_argument(
        "--reset", action="store_true",
        help="Tom databasen och borja om fran scratch"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Parsa och chunka men skriv ingenting till databasen"
    )
    parser.add_argument(
        "--limit", type=int, metavar="N",
        help="Bearbeta bara de N forsta volymerna (for test)"
    )
    parser.add_argument(
        "--volym", type=str, metavar="VOLYM_ID",
        help="Bearbeta bara en volym, t.ex. roa_1789_2_"
    )
    parser.add_argument(
        "--no-embed", action="store_true",
        help="Hoppa over embedding-steget (snabbt test av parsing och DB)"
    )
    args = parser.parse_args()

    if not VOLUMES_JSON.exists():
        log.error("volumes.json saknas -- kor 01_crawl_volumes.py --step2 forst")
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
            log.info(
                "Ansluten till PostgreSQL (%s@%s:%s/%s)",
                PGUSER, PGHOST, PGPORT, PGDATABASE,
            )
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
        meta = volumes_index.get(vid, {"volym_id": vid})

        if conn and not args.reset and already_indexed(conn, vid):
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
    log.info("Klar! %d chunks indexerade under denna korning", total_chunks)
    log.info("Hoppade over (redan indexerade): %d volymer", skipped)
    log.info("Fel: %d volymer", errors)
    log.info(
        "Tid: %.1f min (%.1f sek/vol)",
        elapsed / 60,
        elapsed / max(len(all_files), 1),
    )

    if conn:
        db_stats(conn)
        conn.close()


if __name__ == "__main__":
    main()
