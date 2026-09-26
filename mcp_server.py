# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Magnus Kolsjö
# Se LICENSE-filen i repots rot för fullständig licenstext.

"""
mcp_server.py — MCP-server för KB:s riksdagstryck 1521–1866

Exponerar fyra verktyg till MCP-kompatibla AI-verktyg:
  kb_search       — hybridsökning (fulltextsökning + semantisk sökning)
  kb_get_chunk    — hela textstycket bakom en sökträff, valfritt med omgivning
  kb_get_volume   — metadata och utdrag för en specifik volym
  kb_list_volumes — lista indexerade volymer

Svarsstorlek och citatgranskning:
  Sökträffar och volymutdrag begränsas till ett teckentak (max_tecken) som alltid
  redovisas i svaret när det slår till. Varje sökträff bär sin adress i korpusen
  (volym_id + chunk_index), och kb_get_chunk hämtar hela textstycket bakom en
  träff — vilket är förutsättningen för att kunna verifiera ett ordagrant citat.

Transport-lägen (styrs via MCP_TRANSPORT i .env, se mcp_transport.py):
  stdio (standard): MCP-klienten startar och hanterar processen direkt.
  http:             Servern lyssnar på MCP_HOST:MCP_PORT (standard 8000).
                    MCP_API_KEY krävs — uppstarten avbryts annars (fail-closed).
                    Klienter autentiserar med Authorization: Bearer <nyckel>.
                    I produktion: lägg en reverse proxy (t.ex. Nginx) framför.

Query-expansion (valfritt, styrs via QUERY_EXPANSION_ENABLED i .env):
  Utökar söktermen med historiska stavningsvarianter och latinska ekvivalenter
  via ett valfritt externt LLM-anrop. Stöder alla OpenAI-kompatibla endpoints
  (t.ex. Claude, OpenAI, Ollama, LM Studio). Prompten i prompts/expansion_prompt.txt
  kan anpassas fritt — se README för detaljer.
"""

import os
import logging
import threading
from pathlib import Path
from typing import Callable, Optional, TypedDict

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from mcp_annotationer import CACHE_HINTAR, LASNING_DB
from mcp_transport import starta

load_dotenv()

# ── Konfiguration ─────────────────────────────────────────────────────────────

# Databasanslutning via en enda DATABASE_URL.
# Format: postgresql://anvandare:losenord@localhost:5432/riksdag
DATABASE_URL    = os.getenv("DATABASE_URL", "")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "KBLab/sentence-bert-swedish-cased")

# Transport och autentisering (stdio/http, MCP_API_KEY) hanteras av mcp_transport.py.

# Query-expansion (valfritt)
QUERY_EXPANSION_ENABLED     = os.getenv("QUERY_EXPANSION_ENABLED", "false").lower() == "true"
QUERY_EXPANSION_BASE_URL    = os.getenv("QUERY_EXPANSION_BASE_URL",  "")
QUERY_EXPANSION_API_KEY     = os.getenv("QUERY_EXPANSION_API_KEY",   "")
QUERY_EXPANSION_MODEL       = os.getenv("QUERY_EXPANSION_MODEL",     "")
QUERY_EXPANSION_PROMPT_FILE = os.getenv(
    "QUERY_EXPANSION_PROMPT_FILE",
    str(Path(__file__).parent / "prompts" / "expansion_prompt.txt"),
)

# Viktning: fulltextsökning vs. semantisk sökning (summa = 1.0)
FTS_WEIGHT = 0.35
VEC_WEIGHT = 0.65

# Teckentak för textutdrag. Chunkarna är ~600 ord (flera tusen tecken), så ett
# helt oavkortat sökresultat med tjugo träffar blir stort. Taken håller svaren
# hanterbara, medan kb_get_chunk ger hela texten när ett citat ska verifieras.
# Sätt till 0 för att stänga av trunkeringen helt.
MAX_TECKEN_TRAFF  = int(os.getenv("KB_MAX_TECKEN_TRAFF",  "1500"))
MAX_TECKEN_UTDRAG = int(os.getenv("KB_MAX_TECKEN_UTDRAG", "2000"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Lazy-laddade resurser ─────────────────────────────────────────────────────

_encoder = None
_encoder_lock = threading.Lock()


def get_encoder():
    """Ladda SentenceTransformer-modellen (en gång per process).

    Synkrona verktyg körs på arbetstrådar i mcp 2.x, så flera sökanrop kan
    nå den här funktionen samtidigt. Dubbelkontrollerad låsning: den snabba
    kontrollen utan lås täcker det vanliga fallet (modellen redan laddad);
    låset tas bara av den tråd som faktiskt behöver ladda modellen, och den
    andra kontrollen innanför låset förhindrar att två trådar som båda hann
    förbi den första kontrollen laddar modellen var för sig.
    """
    global _encoder
    if _encoder is None:
        with _encoder_lock:
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


def _ar_postgres() -> bool:
    """Returnerar True — den här servern använder alltid PostgreSQL."""
    return True


def _hamta_db() -> psycopg2.extensions.connection:
    """Öppnar en ny databasanslutning per anrop. Anroparen ansvarar för att stänga den."""
    if not DATABASE_URL:
        raise ValueError(
            "DATABASE_URL är inte satt i .env. "
            "Exempel: postgresql://anvandare:losenord@localhost:5432/riksdag"
        )
    return psycopg2.connect(DATABASE_URL)


def _ph() -> str:
    """Platshållare för parameterbindning — PostgreSQL använder %s."""
    return "%s"


def _prefix() -> str:
    """Schemaprefix för tabellnamn — PostgreSQL: kb_riksdagstryck."""
    return "kb_riksdagstryck."


def initiera_schema() -> None:
    """Säkerställ att schemat finns (idempotent). Robust mot tillfälligt DB-bortfall."""
    try:
        conn = _hamta_db()
        with conn.cursor() as cur:
            cur.execute("CREATE SCHEMA IF NOT EXISTS kb_riksdagstryck")
        conn.commit()
        conn.close()
        log.info("Schema kb_riksdagstryck verifierat")
    except Exception as exc:
        log.warning("Schema-init misslyckades (servern fortsätter ändå): %s", exc)


def embed_query(query: str) -> list:
    """Generera en normaliserad embeddingvektor för en söksträng.

    Täcker även fel i get_encoder() (modellen laddas här, första gången den
    behövs) — ett nätverksfel eller en trasig modellcache ska ge ett
    begripligt ToolError, inte en okommenterad krasch.
    """
    try:
        vec = get_encoder().encode(
            [query],
            normalize_embeddings=True,
            show_progress_bar=False,
        )
    except Exception as exc:
        log.error("embed_query: kunde inte generera embedding: %s", exc)
        raise ToolError(
            f"Kunde inte generera sökembeddingen (embeddingmodellen: {exc})."
        ) from exc
    return vec[0].tolist()


def vec_to_pg(vec: list) -> str:
    """Konvertera en Python-lista till PostgreSQL vector-literal."""
    return "[" + ",".join(f"{v:.8f}" for v in vec) + "]"


# ── Textutdrag och trunkering ─────────────────────────────────────────────────

def _tal(n: int) -> str:
    """
    Formaterar ett heltal med svensk tusentalsavgränsare.

    Avgränsaren är ett hårt blanksteg (U+00A0) enligt svensk skrivregel — skrivet
    som escape-sekvens eftersom tecknet annars inte går att skilja från ett
    vanligt mellanslag i källkoden.
    """
    return f"{n:,}".replace(",", "\u00a0")


def _skar_ut(
    text: str,
    max_tecken: int,
    fran_tecken: int = 0,
    anvisning: Callable[[int], str] | None = None,
) -> str:
    """
    Skär ut ett textutdrag och markera alltid när något har kapats.

    Trunkering utan markör är den allvarligaste formen av tyst datafel — svaret
    ser ut att vara hela innehållet. Därför avslutas ett kapat utdrag alltid med
    en rad som anger hur mycket som visas, hur mycket som finns, och hur resten
    hämtas.

    Parametrar:
      text        — hela texten
      max_tecken  — teckentak; 0 eller negativt betyder ingen trunkering
      fran_tecken — starta utdraget vid denna teckenposition (för paginering)
      anvisning   — funktion som tar utdragets FAKTISKA slutposition och
                    returnerar anropsexemplet för att hämta resten. Anropas
                    bara när utdraget faktiskt är kapat. Positionen måste
                    komma härifrån (inte fran_tecken + max_tecken): kapningen
                    backar till närmaste ord- eller radgräns, så utdraget kan
                    bli kortare än max_tecken — en fortsättning vid
                    fran_tecken + max_tecken skulle då hoppa över det
                    avkapade ordet.

    Klipper alltid på ord- eller radgräns, aldrig mitt i ett ord.
    """
    totalt = len(text)
    start  = max(0, min(fran_tecken, totalt))
    rest   = text[start:]

    kapad_i_slutet = bool(max_tecken and max_tecken > 0 and len(rest) > max_tecken)
    if kapad_i_slutet:
        utdrag = rest[:max_tecken]
        # Backa till närmaste ord- eller radgräns så inget ord klyvs. Om ingen
        # gräns finns rimligt nära slutet behålls den hårda kapningen.
        brytpunkt = max(utdrag.rfind(" "), utdrag.rfind("\n"))
        if brytpunkt > max_tecken * 0.6:
            utdrag = utdrag[:brytpunkt]
        # Ett utdrag som bara blir blanktecken skulle ge slut == start, och
        # anvisningen skulle då peka på samma ställe igen.
        utdrag = utdrag.rstrip() or rest[:max_tecken]
    else:
        utdrag = rest

    if not kapad_i_slutet and start == 0:
        return utdrag

    slut = start + len(utdrag)
    noter = [f"Visar tecken {_tal(start + 1)}–{_tal(slut)} av {_tal(totalt)}"]
    if kapad_i_slutet and anvisning is not None:
        noter.append(anvisning(slut))
    return utdrag + "\n\n[" + ". ".join(noter) + "]"


# ── Query-expansion ───────────────────────────────────────────────────────────

def expandera_fraga(query: str) -> list:
    """
    Expandera söktermen med historiska stavningsvarianter och latinska ekvivalenter.

    Returnerar en lista med kompletterande söktermer, eller tom lista om
    query-expansion är inaktiverat eller misslyckas.

    Aktiveras via QUERY_EXPANSION_ENABLED=true i .env. Stöder alla
    OpenAI-kompatibla endpoints — sätt QUERY_EXPANSION_BASE_URL,
    QUERY_EXPANSION_API_KEY och QUERY_EXPANSION_MODEL för din leverantör.

    Promptfilen (prompts/expansion_prompt.txt) kan redigeras fritt för att
    anpassa expansionen till specifikt material eller tidsperiod.
    """
    if not QUERY_EXPANSION_ENABLED:
        return []

    prompt_path = Path(QUERY_EXPANSION_PROMPT_FILE)
    if not prompt_path.exists():
        log.warning("Promptfil för query-expansion saknas: %s", prompt_path)
        return []

    try:
        from openai import OpenAI

        prompt_template = prompt_path.read_text(encoding="utf-8")
        prompt = prompt_template.format(query=query)

        client = OpenAI(
            base_url=QUERY_EXPANSION_BASE_URL or None,
            api_key=QUERY_EXPANSION_API_KEY or "placeholder",
        )
        response = client.chat.completions.create(
            model=QUERY_EXPANSION_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=120,
            temperature=0.1,
        )
        raw   = response.choices[0].message.content.strip()
        terms = [t.strip() for t in raw.split(",") if t.strip()]
        log.info("Query-expansion: %r → %s", query, terms)
        return terms[:8]  # Begränsa till 8 extra termer

    except Exception as exc:
        log.warning("Query-expansion misslyckades (fortsätter utan): %s", exc)
        return []


# ── Svarstyper ─────────────────────────────────────────────────────────────────
#
# Alla fyra verktyg är steg i samma citeringskedja: kb_search hittar en
# adress (volym_id + chunk_index), kb_get_chunk och kb_get_volume löser upp
# den till text. Typade svar gör adressfälten maskinläsbara för den kedjan
# i stället för att de bara syns i en formaterad textrad. Fält som saknas i
# äldre eller ofullständiga poster (titel, år, stånd, URL, antal textstycken)
# är X | None, eftersom källmaterialet ofta har luckor.


class KbTraff(TypedDict):
    """En sökträff ur kb_search. volym_id + chunk_index är adressen kb_get_chunk tar emot."""

    volym_id: str
    titel: str | None
    ar_fran: int | None
    ar_till: int | None
    stand: str | None
    chunk_index: int
    chunk_antal: int | None
    xml_url: str | None
    pdf_only: bool
    utdrag: str
    trunkerat: bool


class KbSokResultat(TypedDict):
    """Svaret från kb_search."""

    query: str
    year_from: int | None
    year_to: int | None
    stand: str | None
    antal: int
    traffar: list[KbTraff]
    nagot_trunkerat: bool


class KbStycke(TypedDict):
    """Svaret från kb_get_chunk — hela textstycket bakom en sökträff."""

    volym_id: str
    titel: str | None
    ar_fran: int | None
    ar_till: int | None
    stand: str | None
    xml_url: str | None
    pdf_only: bool
    begart_chunk_index: int
    chunk_fran: int
    chunk_till: int
    chunk_antal: int | None
    char_start: int | None
    char_end: int | None
    text: str
    trunkerat: bool


class KbVolym(TypedDict):
    """Svaret från kb_get_volume — metadata och ett utdrag ur första textstycket."""

    volym_id: str
    titel: str | None
    ar_fran: int | None
    ar_till: int | None
    stand: str | None
    xml_url: str | None
    pdf_only: bool
    chunk_antal: int | None
    indexerad_vid: str | None
    forsta_chunk_index: int | None
    utdrag: str | None
    trunkerat: bool


class KbVolymRad(TypedDict):
    """En rad i listan från kb_list_volumes."""

    volym_id: str
    titel: str | None
    ar_fran: int | None
    ar_till: int | None
    stand: str | None
    chunks: int
    pdf_only: bool


class KbVolymLista(TypedDict):
    """Svaret från kb_list_volumes.

    Sidindelad: `volymer` innehåller högst `max_antal` rader från och med
    `fran_position` i den filtrerade, sorterade träffmängden. Ett ofiltrerat
    anrop matchar alla 2 447 deldokument — utan tak skulle svaret (text och
    structuredContent tillsammans) hamna över MCP:s ~1 MB-gräns.
    """

    year_from: int | None
    year_to: int | None
    stand: str | None
    max_antal: int
    fran_position: int
    antal: int
    totalt_matchande: int
    har_fler: bool
    nasta_position: int | None
    volymer: list[KbVolymRad]


# ── MCP-server ─────────────────────────────────────────────────────────────────

mcp = MCPServer(
    "KB Riksdagstryck 1521–1866",
    version="3.0.0",
    cache_hints=CACHE_HINTAR,
    instructions=(
        "MCP-server för ståndsriksdagens handlingar 1521–1866 ur Kungliga "
        "bibliotekets digitaliserade riksdagstryck. Verktygen har prefixet kb_. "
        "RÄKNESÄTT: fältet volym_id avser ett DELDOKUMENT, inte en bibliografisk "
        "volym. KB:s 1 188 volymer är uppdelade i 2 447 deldokument med olika "
        "sidintervall; delarna slutar på __01, __02, __03 och så vidare. "
        "SÖKTERMER: kb_search behandlar hela frågan som en fras — fulltextledet "
        "kräver att samtliga ord förekommer, och kommatecken ignoreras. Ett "
        "komma betyder alltså INTE 'eller' här, till skillnad från de nordiska "
        "servrarna; sök en term i taget när du vill ha alternativ. "
        "SVARSSTORLEK: sökträffar kapas vid ett teckentak som alltid redovisas i "
        "svaret, eftersom ett textstycke är ~600 ord. "
        "CITAT: varje träff bär sin adress (volym_id + chunk_index). Ett "
        "ordagrant citat får aldrig bygga på ett kapat sökutdrag — hämta hela "
        "textstycket med kb_get_chunk först, och använd kontext=1 när en mening "
        "löper över en styckegräns. "
        "SPRÅK: materialet är vetenskapliga editioner med 1500–1800-talssvenska "
        "och latin. Expandera moderna termer till tidens begrepp före sökning."
    ),
)


@mcp.tool(title="Sök i riksdagstrycket", annotations=LASNING_DB)
def kb_search(
    query: str,
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    stand: Optional[str] = None,
    limit: int = 5,
    max_tecken: int = -1,
) -> KbSokResultat:
    """
    Sök i ståndsriksdagens handlingar (1521–1866) med hybridsökning.

    Kombinerar fulltextsökning med svensk stemming (t.ex. "riksdag" matchar
    "riksdagen" och "riksdagens") och semantisk vektorsökning. Resultaten
    rankas efter en viktad kombination av de två poängen.

    SÖKTERMER: hela frågan behandlas som EN fras. Fulltextledet kräver att
    samtliga ord förekommer (AND), och kommatecken har ingen särskild betydelse —
    de ignoreras, så "tryckfrihet, censur" söker efter poster som innehåller
    BÅDA orden. Vill du söka det ena ELLER det andra: gör ett anrop per term.
    Semantikledet använder alltid hela frasen som den är. Detta skiljer sig från
    de nordiska servrarna, där komma betyder OR.

    Parametrar:
      query      — sökfras på svenska (eller latin för äldre material)
      year_from  — filtrera från och med detta år (t.ex. 1700)
      year_to    — filtrera till och med detta år (t.ex. 1800)
      stand      — filtrera på stånd: adel, praster, borgare, bonder,
                   bihang, riksdagsbeslut, samt register (sak- och
                   personregister) och okant (KU-handlingar 1809–1815).
                   Utelämna för alla stånd.
      limit      — max antal resultat, 1–20 (standard: 5)
      max_tecken — teckentak per träff (standard: KB_MAX_TECKEN_TRAFF, 1500).
                   Sätt 0 för hela textstycket utan trunkering. Varje kapat
                   utdrag markeras i svaret med hur mycket som visas av hur mycket.

    Returnerar de bäst matchande textutdragen med källa och adress i korpusen.
    Textutdragen visas alltid i originalets stavning.

    CITAT: varje träff anger sin adress (volym-ID och chunk-nummer). Innan en
    lagtext eller ett protokollcitat återges ordagrant — hämta hela textstycket
    med kb_get_chunk(volym_id, chunk_index). Ett sökutdrag kan vara kapat, och
    ett citat får aldrig bygga på ett kapat utdrag.
    """
    limit = min(max(1, limit), 20)
    if max_tecken < 0:
        max_tecken = MAX_TECKEN_TRAFF

    # Expandera söktermen med historiska varianter om aktiverat
    extra_terms = expandera_fraga(query)
    fts_query   = (query + " " + " ".join(extra_terms)).strip() if extra_terms else query

    # Generera embedding och bygg vector-literal
    query_vec   = embed_query(query)   # embedden baseras alltid på originaltermen
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
            FROM {_prefix()}riksdag_chunks c
            {where}
              AND c.fts_vector @@ plainto_tsquery('swedish', %s)
            ORDER BY fts_score DESC
            LIMIT 100
        ),
        vec_hits AS (
            SELECT c.id,
                   1 - (c.embedding <=> %s::vector) AS vec_score
            FROM {_prefix()}riksdag_chunks c
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
            c.chunk_text                                                   AS utdrag,
            iv.chunk_antal,
            COALESCE(f.fts_score, 0)                                       AS fts_score,
            COALESCE(v.vec_score, 1 - (c.embedding <=> %s::vector))        AS vec_score,
            COALESCE(f.fts_score, 0) * {FTS_WEIGHT}
              + COALESCE(v.vec_score, 1 - (c.embedding <=> %s::vector)) * {VEC_WEIGHT}
                                                                           AS combined_score
        FROM {_prefix()}riksdag_chunks c
        JOIN candidates        ON c.id = candidates.id
        LEFT JOIN fts_hits f   ON c.id = f.id
        LEFT JOIN vec_hits v   ON c.id = v.id
        LEFT JOIN {_prefix()}indexerade_volymer iv ON iv.volym_id = c.volym_id
        ORDER BY combined_score DESC
        LIMIT %s
    """

    full_params = (
        [fts_query] + params + [fts_query]              # fts_hits CTE: ts_rank, WHERE-filter, AND-fts
        + [vec_literal] + params + [vec_literal]         # vec_hits CTE: embedding <=> i SELECT, WHERE-filter, embedding <=> i ORDER BY
        + [vec_literal, vec_literal, limit]              # SELECT: embedding <=> i COALESCE×2, LIMIT
    )

    try:
        conn = _hamta_db()
    except ValueError as exc:
        raise ToolError(str(exc)) from exc

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(sql, full_params)
            rows = cur.fetchall()
    except Exception as exc:
        log.error("kb_search SQL-fel: %s", exc)
        raise ToolError(
            "Sökningen mot databasen misslyckades. Kontrollera att databasen är "
            "igång och att schemat kb_riksdagstryck är indexerat."
        ) from exc
    finally:
        conn.close()

    traffar: list[KbTraff] = []
    nagot_trunkerat = False

    for row in rows:
        chunk_index = row["chunk_index"]
        utdrag_hela = row["utdrag"] or ""
        anvisning = (
            lambda _slut, vid=row["volym_id"], ci=chunk_index:
                f'Hela textstycket: kb_get_chunk("{vid}", {ci})'
        )
        utdrag = _skar_ut(utdrag_hela, max_tecken, anvisning=anvisning)
        traff_trunkerad = len(utdrag) != len(utdrag_hela)
        nagot_trunkerat = nagot_trunkerat or traff_trunkerad

        traffar.append(
            KbTraff(
                volym_id=row["volym_id"],
                titel=row["titel"],
                ar_fran=row["ar_fran"],
                ar_till=row["ar_till"],
                stand=row["stand"],
                chunk_index=chunk_index,
                chunk_antal=row["chunk_antal"],
                xml_url=row["xml_url"],
                pdf_only=bool(row["pdf_only"]),
                utdrag=utdrag,
                trunkerat=traff_trunkerad,
            )
        )

    return KbSokResultat(
        query=query,
        year_from=year_from,
        year_to=year_to,
        stand=stand,
        antal=len(traffar),
        traffar=traffar,
        nagot_trunkerat=nagot_trunkerat,
    )


@mcp.tool(title="Hämta textstycke", annotations=LASNING_DB)
def kb_get_chunk(
    volym_id: str,
    chunk_index: int,
    kontext: int = 0,
    max_tecken: int = 0,
    fran_tecken: int = 0,
) -> KbStycke:
    """
    Hämta hela textstycket bakom en sökträff, valfritt med omgivande stycken.

    Detta är verktyget för citatgranskning. Ett utdrag i kb_search kan vara
    kapat vid teckentaket; här får du hela texten och kan verifiera att ett
    citat är fullständigt och korrekt återgivet.

    Parametrar:
      volym_id    — volymens ID ur en sökträff, t.ex. "rda_1521-1560___01"
      chunk_index — textstyckets nummer ur en sökträff (fältet "Chunk")
      kontext     — hämta även så här många stycken före och efter (standard 0).
                    Använd 1 när en mening eller paragraf löper över en
                    styckegräns.
      max_tecken  — teckentak (standard 0 = hela texten utan trunkering)
      fran_tecken — börja utdraget vid denna teckenposition, för att bläddra
                    vidare i en text som kapats av max_tecken

    Returnerar textstyckets fulltext i originalets stavning, med volymens
    metadata och styckets position i volymen.
    """
    kontext = min(max(0, kontext), 5)
    fran = chunk_index - kontext
    till = chunk_index + kontext

    try:
        conn = _hamta_db()
    except ValueError as exc:
        raise ToolError(str(exc)) from exc

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(
                f"""SELECT c.chunk_index, c.chunk_text, c.titel, c.ar_fran,
                           c.ar_till, c.stand, c.xml_url, c.pdf_only,
                           c.char_start, c.char_end, iv.chunk_antal
                    FROM {_prefix()}riksdag_chunks c
                    LEFT JOIN {_prefix()}indexerade_volymer iv
                           ON iv.volym_id = c.volym_id
                    WHERE c.volym_id = %s
                      AND c.chunk_index BETWEEN %s AND %s
                    ORDER BY c.chunk_index""",
                (volym_id, fran, till),
            )
            rader = cur.fetchall()
    except Exception as exc:
        log.error("kb_get_chunk SQL-fel: %s", exc)
        raise ToolError(
            "Hämtningen av textstycket misslyckades mot databasen. Kontrollera "
            "att databasen är igång."
        ) from exc
    finally:
        conn.close()

    if not rader:
        # Skilj "okänd volym" från "giltig volym, men chunk-numret finns inte" —
        # ett felmeddelande ska visa vägen framåt, inte bara konstatera fel.
        # Hela uppslaget (även själva anslutningen) är skyddat: går den inte
        # att öppna behandlas det som "kunde inte avgöra" i stället för att
        # krascha med ett okommenterat undantag.
        try:
            conn2 = _hamta_db()
            try:
                with conn2.cursor() as cur:
                    cur.execute(
                        f"SELECT chunk_antal FROM {_prefix()}indexerade_volymer "
                        f"WHERE volym_id = %s",
                        (volym_id,),
                    )
                    vol = cur.fetchone()
            finally:
                conn2.close()
        except Exception:
            vol = None

        if not vol:
            raise ToolError(
                f"Volymen '{volym_id}' finns inte i databasen. "
                "Använd kb_list_volumes() för att se tillgängliga volym-ID:n."
            )
        raise ToolError(
            f"Volymen '{volym_id}' finns, men har inget textstycke med "
            f"chunk_index {chunk_index}. Volymen har {vol[0]} stycken "
            "(numrerade från 0)."
        )

    meta = rader[0]

    rader_text = []
    for r in rader:
        if len(rader) > 1:
            markor = " ←" if r["chunk_index"] == chunk_index else ""
            rader_text.append(f"── Chunk {r['chunk_index']}{markor} ──")
        rader_text.append(r["chunk_text"] or "")
    text_hela = "\n".join(rader_text)

    anvisning = (
        lambda slut: f'Fortsätt: kb_get_chunk("{volym_id}", {chunk_index}, fran_tecken={slut})'
    )

    text = _skar_ut(text_hela, max_tecken, fran_tecken, anvisning)

    char_end_sista = rader[-1]["char_end"] if rader[-1]["char_end"] is not None else meta["char_end"]

    return KbStycke(
        volym_id=volym_id,
        titel=meta["titel"],
        ar_fran=meta["ar_fran"],
        ar_till=meta["ar_till"],
        stand=meta["stand"],
        xml_url=meta["xml_url"],
        pdf_only=bool(meta["pdf_only"]),
        begart_chunk_index=chunk_index,
        chunk_fran=rader[0]["chunk_index"],
        chunk_till=rader[-1]["chunk_index"],
        chunk_antal=meta["chunk_antal"],
        char_start=meta["char_start"],
        char_end=char_end_sista,
        text=text,
        trunkerat=len(text) != len(text_hela),
    )


@mcp.tool(title="Hämta volymöversikt", annotations=LASNING_DB)
def kb_get_volume(
    volym_id: str,
    max_tecken: int = -1,
    fran_tecken: int = 0,
) -> KbVolym:
    """
    Hämta metadata och ett textutdrag ur början av en specifik volym.

    Parametrar:
      volym_id    — volymens ID, t.ex. "rda_1521-1560___01"
                    (använd kb_list_volumes för att se tillgängliga ID:n)
      max_tecken  — teckentak för utdraget (standard: KB_MAX_TECKEN_UTDRAG, 2000).
                    Sätt 0 för hela första textstycket.
      fran_tecken — börja utdraget vid denna teckenposition inom första stycket

    Returnerar titel, år, stånd, antal textstycken och ett utdrag ur det första.
    Utdraget visas i originalets stavning.

    OBS: utdraget kommer ur volymens FÖRSTA textstycke — det är en översikt, inte
    en väg till innehållet längre in i volymen. För att läsa vidare, använd
    kb_get_chunk(volym_id, chunk_index) med stigande chunk_index, eller sök inom
    volymen med kb_search.
    """
    if max_tecken < 0:
        max_tecken = MAX_TECKEN_UTDRAG

    try:
        conn = _hamta_db()
    except ValueError as exc:
        raise ToolError(str(exc)) from exc

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(
                f"SELECT chunk_antal, indexerad_vid "
                f"FROM {_prefix()}indexerade_volymer WHERE volym_id = %s",
                (volym_id,),
            )
            vol_row = cur.fetchone()

            if not vol_row:
                raise ToolError(
                    f"Volym '{volym_id}' finns inte i databasen. "
                    "Använd kb_list_volumes() för att se tillgängliga volymer."
                )

            cur.execute(
                f"""SELECT titel, ar_fran, ar_till, stand, xml_url, pdf_only
                FROM {_prefix()}riksdag_chunks
                WHERE volym_id = %s LIMIT 1""",
                (volym_id,),
            )
            meta = cur.fetchone()

            cur.execute(
                f"""SELECT chunk_text, chunk_index FROM {_prefix()}riksdag_chunks
                WHERE volym_id = %s ORDER BY chunk_index LIMIT 1""",
                (volym_id,),
            )
            first = cur.fetchone()

    except ToolError:
        raise
    except Exception as exc:
        log.error("kb_get_volume SQL-fel: %s", exc)
        raise ToolError(
            "Hämtningen av volymen misslyckades mot databasen. Kontrollera att "
            "databasen är igång."
        ) from exc
    finally:
        conn.close()

    _indexerad_vid_rad = vol_row["indexerad_vid"]
    indexerad_vid = (
        _indexerad_vid_rad.strftime("%Y-%m-%d %H:%M") if _indexerad_vid_rad else None
    )

    utdrag = None
    trunkerat = False
    forsta_index = None
    if first:
        forsta_index = first["chunk_index"] if first["chunk_index"] is not None else 0
        anvisning = (
            lambda _slut: f'Hela stycket: kb_get_chunk("{volym_id}", {forsta_index}, max_tecken=0)'
        )
        text_hela = first["chunk_text"] or ""
        utdrag = _skar_ut(text_hela, max_tecken, fran_tecken, anvisning)
        trunkerat = len(utdrag) != len(text_hela)

    return KbVolym(
        volym_id=volym_id,
        titel=meta["titel"] if meta else None,
        ar_fran=meta["ar_fran"] if meta else None,
        ar_till=meta["ar_till"] if meta else None,
        stand=meta["stand"] if meta else None,
        xml_url=meta["xml_url"] if meta else None,
        pdf_only=bool(meta["pdf_only"]) if meta else False,
        chunk_antal=vol_row["chunk_antal"],
        indexerad_vid=indexerad_vid,
        forsta_chunk_index=forsta_index,
        utdrag=utdrag,
        trunkerat=trunkerat,
    )


MAX_ANTAL_VOLYMER_DEFAULT = 500
MAX_ANTAL_VOLYMER_TAK = 1000


@mcp.tool(title="Lista volymer", annotations=LASNING_DB)
def kb_list_volumes(
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    stand: Optional[str] = None,
    max_antal: int = MAX_ANTAL_VOLYMER_DEFAULT,
    fran_position: int = 0,
) -> KbVolymLista:
    """
    Lista indexerade deldokument i databasen.

    OBS om räknesättet: KB:s katalog beskriver 1 188 bibliografiska volymer, men en
    volym är ofta uppdelad i flera filer med olika sidintervall. Det här verktyget —
    och fältet volym_id överallt i servern — avser **deldokument**, av vilka det finns
    2 447. Ett volym_id som "rda_1521-1560___01" är första delen av den bibliografiska
    volymen "rda_1521-1560"; delarna ligger i följd och slutar på __01, __02, __03 …

    Parametrar:
      year_from     — visa bara deldokument vars startår är >= detta värde
      year_to       — visa bara deldokument vars slutår är <= detta värde
      stand         — filtrera på stånd: adel, praster, borgare, bonder,
                      bihang, riksdagsbeslut, samt register (sak- och
                      personregister) och okant (KU-handlingar 1809–1815).
                      Utelämna för alla stånd.
      max_antal     — max antal rader i svaret, 1–1000 (standard 500). Ett
                      ofiltrerat anrop matchar alla 2 447 deldokument, så
                      svaret sidindelas alltid.
      fran_position — hoppa över så här många rader i den sorterade
                      träffmängden (för att hämta nästa sida).

    Returnerar en sida av den sorterade listan (volym-ID, år, stånd, antal
    textstycken), samt `totalt_matchande` och `har_fler`/`nasta_position` för
    att hämta resten.
    """
    max_antal = min(max(1, max_antal), MAX_ANTAL_VOLYMER_TAK)
    fran_position = max(0, fran_position)

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

    # COUNT(*) OVER() ger totalt antal matchande rader i samma fråga som
    # sidan hämtas, så att har_fler kan avgöras utan en andra rundtripp.
    sql = f"""
        WITH grupperat AS (
            SELECT
                volym_id,
                MAX(titel)          AS titel,
                MIN(ar_fran)        AS ar_fran,
                MAX(ar_till)        AS ar_till,
                MAX(stand)          AS stand,
                COUNT(*)            AS chunks,
                BOOL_OR(pdf_only)   AS pdf_only
            FROM {_prefix()}riksdag_chunks
            {where}
            GROUP BY volym_id
        )
        SELECT *, COUNT(*) OVER() AS totalt_matchande
        FROM grupperat
        ORDER BY ar_fran NULLS LAST, volym_id
        LIMIT %s OFFSET %s
    """

    try:
        conn = _hamta_db()
    except ValueError as exc:
        raise ToolError(str(exc)) from exc

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(sql, params + [max_antal, fran_position])
            rows = cur.fetchall()
    except Exception as exc:
        log.error("kb_list_volumes SQL-fel: %s", exc)
        raise ToolError(
            "Listningen av volymer misslyckades mot databasen. Kontrollera att "
            "databasen är igång."
        ) from exc
    finally:
        conn.close()

    volymer = [
        KbVolymRad(
            volym_id=row["volym_id"],
            titel=row["titel"],
            ar_fran=row["ar_fran"],
            ar_till=row["ar_till"],
            stand=row["stand"],
            chunks=row["chunks"],
            pdf_only=bool(row["pdf_only"]),
        )
        for row in rows
    ]

    totalt_matchande = rows[0]["totalt_matchande"] if rows else 0
    nasta_position = fran_position + len(volymer)
    har_fler = nasta_position < totalt_matchande

    return KbVolymLista(
        max_antal=max_antal,
        fran_position=fran_position,
        totalt_matchande=totalt_matchande,
        har_fler=har_fler,
        nasta_position=nasta_position if har_fler else None,
        year_from=year_from,
        year_to=year_to,
        stand=stand,
        antal=len(volymer),
        volymer=volymer,
    )


# ── Startpunkt ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    starta(mcp, standardport=8000, initiera=initiera_schema, forvarm_http=get_encoder)
