# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Magnus Kolsjö
# Se LICENSE-filen i repots rot för fullständig licenstext.

"""
mcp_server.py — MCP-server för KB:s riksdagstryck 1521–1866

Exponerar tre verktyg till MCP-kompatibla AI-verktyg:
  kb_search       — hybridsökning (fulltextsökning + semantisk sökning)
  kb_get_volume   — metadata och utdrag för en specifik volym
  kb_list_volumes — lista indexerade volymer

Krav:
  - PostgreSQL med pgvector-extension (kör: docker compose up -d)
  - Konfiguration via .env (se config.example.env)
  - Installerade beroenden: pip install -r requirements.txt

Transport-lägen (styrs via MCP_TRANSPORT i .env):

  stdio (standard, lokal användning):
    python3 mcp_server.py
    MCP-klienten startar och hanterar processen direkt.

  http (hostad driftsättning):
    MCP_TRANSPORT=http python3 mcp_server.py
    Servern lyssnar på MCP_HOST:MCP_PORT (standard 127.0.0.1:8000).
    Sätt MCP_API_KEY för Bearer-token-autentisering.
    I produktion: lägg en reverse proxy (t.ex. Nginx) framför servern.
"""

import os
import logging
from typing import Optional

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()

# ── Konfiguration ─────────────────────────────────────────────────────────────

PGHOST          = os.getenv("PGHOST",     "localhost")
PGPORT          = int(os.getenv("PGPORT", "5432"))
PGDATABASE      = os.getenv("PGDATABASE", "riksdagstryck")
PGUSER          = os.getenv("PGUSER",     "")
PGPASSWORD      = os.getenv("PGPASSWORD", "")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "KBLab/sentence-bert-swedish-cased")

# Transport och autentisering
MCP_TRANSPORT = os.getenv("MCP_TRANSPORT", "stdio").lower()
MCP_HOST      = os.getenv("MCP_HOST",      "127.0.0.1")
MCP_PORT      = int(os.getenv("MCP_PORT",  "8000"))
MCP_API_KEY   = os.getenv("MCP_API_KEY",   "")

# Viktning: fulltextsökning vs. semantisk sökning (summa = 1.0)
FTS_WEIGHT = 0.35
VEC_WEIGHT = 0.65

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Lazy-laddade resurser ─────────────────────────────────────────────────────

_encoder = None
_conn: Optional[psycopg2.extensions.connection] = None


def get_encoder():
    """
    Ladda SentenceTransformer-modellen (en gång per process).
    Väljer automatiskt MPS, CUDA eller CPU.
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
        log.info("Embedding-modell laddad")
    return _encoder


def get_conn() -> psycopg2.extensions.connection:
    """
    Returnera en öppen databasanslutning. Återansluter automatiskt vid
    stängd eller bruten anslutning.
    """
    global _conn
    if _conn is None or _conn.closed:
        _conn = psycopg2.connect(
            host=PGHOST, port=PGPORT,
            dbname=PGDATABASE, user=PGUSER, password=PGPASSWORD,
        )
        _conn.autocommit = True
        log.info(
            "Ansluten till PostgreSQL (%s@%s:%s/%s)",
            PGUSER, PGHOST, PGPORT, PGDATABASE,
        )
    return _conn


def embed_query(query: str) -> list:
    """Generera en normaliserad embeddingvektor för en söksträng."""
    vec = get_encoder().encode(
        [query],
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return vec[0].tolist()


def vec_to_pg(vec: list) -> str:
    """Konvertera en Python-lista till PostgreSQL vector-literal."""
    return "[" + ",".join(f"{v:.8f}" for v in vec) + "]"


# ── MCP-server ─────────────────────────────────────────────────────────────────

mcp = FastMCP("KB Riksdagstryck 1521–1866")


@mcp.tool()
def kb_search(
    query: str,
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    stand: Optional[str] = None,
    limit: int = 5,
) -> str:
    """
    Sök i ståndsriksdagens handlingar (1521–1866) med hybridsökning.

    Kombinerar fulltextsökning med svensk stemming (t.ex. "riksdag" matchar
    "riksdagen" och "riksdagens") och semantisk vektorsökning. Resultaten
    rankas efter en viktad kombination av de två poängen.

    Parametrar:
      query     — sökfras på svenska (eller latin för äldre material)
      year_from — filtrera från och med detta år (t.ex. 1700)
      year_to   — filtrera till och med detta år (t.ex. 1800)
      stand     — filtrera på stånd: adel, praster, borgare, bonder,
                  bihang, riksdagsbeslut (utelämna för alla stånd)
      limit     — max antal resultat, 1–20 (standard: 5)

    Returnerar de bäst matchande textutdragen med källa och poäng.
    """
    limit = min(max(1, limit), 20)

    # Generera embedding och bygg vector-literal
    query_vec = embed_query(query)
    vec_literal = vec_to_pg(query_vec)

    # Bygg WHERE-villkor för valfria filter
    conditions = ["c.embedding IS NOT NULL"]
    params: list = []

    if year_from is not None:
        conditions.append("c.ar_fran >= %s")
        params.append(year_from)
    if year_to is not None:
        conditions.append("c.ar_till <= %s")
        params.append(year_to)
    if stand:
        conditions.append("c.stand = %s")
        params.append(stand.lower())

    where = "WHERE " + " AND ".join(conditions)

    # Tvåfas hybridsökning:
    #   Fas 1: hämta de 100 bästa FTS-träffarna och de 100 bästa vektor-träffarna.
    #          GIN-indexet används för FTS, HNSW-indexet för vektorsökning.
    #   Fas 2: slå ihop kandidaterna, beräkna kombinerat poäng, returnera topp limit.

    sql = f"""
        WITH fts_hits AS (
            SELECT c.id,
                   ts_rank(c.fts_vector, plainto_tsquery('swedish', %s)) AS fts_score
            FROM riksdag_chunks c
            {where}
              AND c.fts_vector @@ plainto_tsquery('swedish', %s)
            ORDER BY fts_score DESC
            LIMIT 100
        ),
        vec_hits AS (
            SELECT c.id,
                   1 - (c.embedding <=> %s::vector) AS vec_score
            FROM riksdag_chunks c
            {where}
            ORDER BY c.embedding <=> %s::vector
            LIMIT 100
        ),
        candidates AS (
            SELECT id FROM fts_hits
            UNION
            SELECT id FROM vec_hits
        )
        SELECT
            c.volym_id,
            c.titel,
            c.ar_fran,
            c.ar_till,
            c.stand,
            c.chunk_index,
            c.xml_url,
            c.pdf_only,
            LEFT(c.chunk_text, 600)                                        AS utdrag,
            COALESCE(f.fts_score, 0)                                       AS fts_score,
            COALESCE(v.vec_score, 1 - (c.embedding <=> %s::vector))        AS vec_score,
            COALESCE(f.fts_score, 0) * {FTS_WEIGHT}
              + COALESCE(v.vec_score, 1 - (c.embedding <=> %s::vector)) * {VEC_WEIGHT}
                                                                           AS combined_score
        FROM riksdag_chunks c
        JOIN candidates        ON c.id = candidates.id
        LEFT JOIN fts_hits f   ON c.id = f.id
        LEFT JOIN vec_hits v   ON c.id = v.id
        ORDER BY combined_score DESC
        LIMIT %s
    """

    # Params-ordning matchar platshållarna i SQL ovan:
    #   fts_hits CTE:  plainto_tsquery ×2, WHERE-filter
    #   vec_hits CTE:  WHERE-filter, vec ×2
    #   SELECT:        vec ×2 (fallback i COALESCE)
    #   LIMIT:         limit
    full_params = (
        [query]          # fts_hits: plainto_tsquery arg 1
        + params         # fts_hits: WHERE-filter
        + [query]        # fts_hits: plainto_tsquery arg 2 (i AND-villkoret)
        + params         # vec_hits: WHERE-filter
        + [vec_literal]  # vec_hits: <=> i SELECT
        + [vec_literal]  # vec_hits: ORDER BY
        + [vec_literal]  # SELECT: vec_score COALESCE-fallback
        + [vec_literal]  # SELECT: combined_score COALESCE-fallback
        + [limit]
    )

    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(sql, full_params)
            rows = cur.fetchall()
    except Exception as exc:
        log.error("kb_search SQL-fel: %s", exc)
        return f"Sökfel: {exc}"

    if not rows:
        filter_parts = []
        if year_from or year_to:
            filter_parts.append(f"år {year_from or '?'}–{year_to or '?'}")
        if stand:
            filter_parts.append(f"stånd: {stand}")
        filter_str = " (" + ", ".join(filter_parts) + ")" if filter_parts else ""
        return f"Inga resultat för {query!r}{filter_str}."

    filter_desc = ""
    if year_from or year_to:
        filter_desc += f" | År: {year_from or '?'}–{year_to or '?'}"
    if stand:
        filter_desc += f" | Stånd: {stand}"

    parts = [f"Sökte: {query!r}{filter_desc} — {len(rows)} resultat\n"]

    for i, row in enumerate(rows, 1):
        ar = (
            f"{row['ar_fran']}–{row['ar_till']}"
            if row["ar_fran"]
            else "okänt år"
        )
        pdf_mark = " [PDF-källa]" if row["pdf_only"] else ""
        parts.append(
            f"━━━ Resultat {i} ━━━\n"
            f"Volym:  {row['volym_id']}{pdf_mark}\n"
            f"Titel:  {row['titel'] or '–'}\n"
            f"År:     {ar}  |  Stånd: {row['stand'] or '–'}  |  Chunk: {row['chunk_index']}\n"
            f"Poäng:  {row['combined_score']:.3f}  "
            f"(FTS: {row['fts_score']:.3f}, Semantisk: {row['vec_score']:.3f})\n"
            f"URL:    {row['xml_url'] or '–'}\n"
            f"\n{row['utdrag']}\n"
        )

    return "\n".join(parts)


@mcp.tool()
def kb_get_volume(volym_id: str) -> str:
    """
    Hämta metadata och ett textutdrag för en specifik volym.

    Parametrar:
      volym_id — volymens ID, t.ex. "rda_1521-1560___01"
                 (använd kb_list_volumes för att se tillgängliga ID:n)

    Returnerar titel, år, stånd, antal chunks och ett utdrag ur första chunken.
    """
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            # Indexeringsstatus och chunk-antal
            cur.execute(
                "SELECT chunk_count, indexed_at FROM indexed_volumes WHERE volym_id = %s",
                (volym_id,),
            )
            vol_row = cur.fetchone()

            if not vol_row:
                return (
                    f"Volym '{volym_id}' finns inte i databasen. "
                    "Använd kb_list_volumes() för att se tillgängliga volymer."
                )

            # Metadata från första chunken (titel, år, stånd, URL)
            cur.execute(
                """
                SELECT titel, ar_fran, ar_till, stand, xml_url, pdf_only
                FROM riksdag_chunks
                WHERE volym_id = %s
                LIMIT 1
                """,
                (volym_id,),
            )
            meta = cur.fetchone()

            # Första chunken som textutdrag
            cur.execute(
                """
                SELECT chunk_text
                FROM riksdag_chunks
                WHERE volym_id = %s
                ORDER BY chunk_index
                LIMIT 1
                """,
                (volym_id,),
            )
            first = cur.fetchone()

    except Exception as exc:
        log.error("kb_get_volume SQL-fel: %s", exc)
        return f"Databasfel: {exc}"

    ar = (
        f"{meta['ar_fran']}–{meta['ar_till']}"
        if meta and meta["ar_fran"]
        else "okänt"
    )
    pdf_mark = " (konverterad från PDF)" if meta and meta["pdf_only"] else ""
    indexed_at = vol_row["indexed_at"].strftime("%Y-%m-%d %H:%M")

    lines = [
        f"Volym:      {volym_id}{pdf_mark}",
        f"Titel:      {meta['titel'] if meta else '–'}",
        f"År:         {ar}",
        f"Stånd:      {meta['stand'] if meta else '–'}",
        f"URL:        {meta['xml_url'] if meta else '–'}",
        f"Chunks:     {vol_row['chunk_count']}",
        f"Indexerad:  {indexed_at}",
    ]

    if first:
        lines.append(f"\nFörsta chunken:\n{first['chunk_text'][:800]}")

    return "\n".join(lines)


@mcp.tool()
def kb_list_volumes(
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    stand: Optional[str] = None,
) -> str:
    """
    Lista indexerade volymer i databasen.

    Parametrar:
      year_from — visa bara volymer vars startår är >= detta värde
      year_to   — visa bara volymer vars slutår är <= detta värde
      stand     — filtrera på stånd: adel, praster, borgare, bonder,
                  bihang, riksdagsbeslut (utelämna för alla stånd)

    Returnerar en sorterad lista med volym-ID, år, stånd och antal chunks.
    """
    conditions: list = []
    params: list = []

    if year_from is not None:
        conditions.append("ar_fran >= %s")
        params.append(year_from)
    if year_to is not None:
        conditions.append("ar_till <= %s")
        params.append(year_to)
    if stand:
        conditions.append("stand = %s")
        params.append(stand.lower())

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    sql = f"""
        SELECT
            volym_id,
            MAX(titel)          AS titel,
            MIN(ar_fran)        AS ar_fran,
            MAX(ar_till)        AS ar_till,
            MAX(stand)          AS stand,
            COUNT(*)            AS chunks,
            BOOL_OR(pdf_only)   AS pdf_only
        FROM riksdag_chunks
        {where}
        GROUP BY volym_id
        ORDER BY MIN(ar_fran) NULLS LAST, volym_id
    """

    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    except Exception as exc:
        log.error("kb_list_volumes SQL-fel: %s", exc)
        return f"Databasfel: {exc}"

    if not rows:
        return "Inga volymer matchade filtret."

    filter_desc = ""
    if year_from or year_to:
        filter_desc += f" | År: {year_from or '?'}–{year_to or '?'}"
    if stand:
        filter_desc += f" | Stånd: {stand}"

    lines = [f"{len(rows)} volymer{filter_desc}:\n"]

    for row in rows:
        ar = (
            f"{row['ar_fran']}–{row['ar_till']}"
            if row["ar_fran"]
            else "okänt"
        )
        pdf_mark = " [PDF]" if row["pdf_only"] else ""
        titel_str = f"  {row['titel']}" if row["titel"] else ""
        lines.append(
            f"{row['volym_id']}{pdf_mark}"
            f"  |  {ar}"
            f"  |  {row['stand'] or '?'}"
            f"  |  {row['chunks']} chunks"
            f"{titel_str}"
        )

    return "\n".join(lines)


# ── HTTP-autentisering ────────────────────────────────────────────────────────

def _make_auth_app(asgi_app, api_key: str):
    """
    Wrap en ASGI-app med enkel Bearer-token-autentisering.
    Alla anrop utan korrekt Authorization-header avvisas med HTTP 401.
    """
    from starlette.applications import Starlette
    from starlette.middleware import Middleware
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import PlainTextResponse
    from starlette.routing import Mount

    class ApiKeyMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            token = (
                request.headers.get("Authorization", "")
                .removeprefix("Bearer ")
                .strip()
            )
            if token != api_key:
                return PlainTextResponse(
                    "Obehörig: ogiltig eller saknad API-nyckel.", status_code=401
                )
            return await call_next(request)

    return Starlette(
        routes=[Mount("/", app=asgi_app)],
        middleware=[Middleware(ApiKeyMiddleware)],
    )


# ── Startpunkt ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if MCP_TRANSPORT == "http":
        import uvicorn

        # Preladda embedding-modellen vid uppstart så att första sökanropet
        # svarar lika snabbt som alla efterföljande. Misslyckas modellen att
        # laddas syns det direkt i loggarna — inte vid det första användaranropet.
        log.info("Preladdar embedding-modell...")
        get_encoder()
        log.info("Embedding-modell redo")

        # Hämta ASGI-appen från FastMCP
        try:
            asgi_app = mcp.streamable_http_app()
        except AttributeError:
            # Äldre version av mcp-biblioteket
            log.warning(
                "mcp.streamable_http_app() saknas — försöker med sse_app(). "
                "Uppgradera mcp-paketet om problem uppstår."
            )
            asgi_app = mcp.sse_app()

        if MCP_API_KEY:
            log.info("API-nyckelautentisering aktiverad")
            app = _make_auth_app(asgi_app, MCP_API_KEY)
        else:
            log.warning(
                "MCP_API_KEY är inte satt — servern körs utan autentisering. "
                "Bind enbart till loopback (MCP_HOST=127.0.0.1) eller "
                "skydda via reverse proxy."
            )
            app = asgi_app

        log.info("Startar HTTP-transport på %s:%s", MCP_HOST, MCP_PORT)
        uvicorn.run(app, host=MCP_HOST, port=MCP_PORT, log_level="info")
    else:
        log.info("Startar stdio-transport (lokal användning)")
        mcp.run()
