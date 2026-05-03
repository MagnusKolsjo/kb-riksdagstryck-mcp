# Äldre riksdagstryck från KB (1521–1866) — MCP-server

Lokal sökbar databas över ståndsriksdagens handlingar 1521–1866, baserad på
Kungliga bibliotekets digitaliserade XML-material. Exponeras som MCP-server till
MCP-kompatibla AI-verktyg.

---

## Vad finns här?

KB har digitaliserat ståndsriksdagens handlingar till ABBYY FineReader 10 XML.
Materialet täcker alla fyra stånd — adel, präster, borgare och bönder — samt
riksdagsbeslut och bihang (bilagor). Totalt 1 188 volymer är kartlagda.

| Kategori | Volymer |
|---|---|
| Adel (ridderskapet) | 349 |
| Bihang (bilagor till protokoll) | 334 |
| Präster | 174 |
| Bönder | 157 |
| Borgare | 130 |
| Riksdagsbeslut | 21 |
| Register | 3 |
| Meta-dokument | 4 |

**16 volymer** från perioden 1746–1847 (frihetstiden, Gustav III:s revolution 1772,
mordet på Gustav III 1792, förlusten av Finland 1809) finns enbart som PDF.
Dessa konverteras till kompatibelt XML av `04_pdf_to_xml.py`.

---

## Krav

- Python 3.11+
- Docker (för PostgreSQL + pgvector)
- ~3–6 GB diskutrymme för råfiler och databas
- Embedding-steget är tidskrävande — räkna med flera timmar

---

## Installation

```bash
# 1. Klona repot
git clone https://github.com/<användarnamn>/kb-riksdagstryck-mcp.git
cd kb-riksdagstryck-mcp

# 2. Skapa virtuell miljö och installera beroenden
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. Konfigurera miljövariabler
cp config.example.env .env
# Öppna .env och fyll i DATABASE_URL med ditt eget användarnamn och lösenord

# 4. Starta PostgreSQL + pgvector
docker compose up -d
# Vänta ~10 sekunder tills databasen är redo
```

---

## Körning — steg för steg

### Steg 1: Kartlägg volymer från KB
```bash
python3 01_crawl_volumes.py
```
Hämtar volymförteckningen från `riksdagstryck.kb.se` och sparar `volumes.json`
och `volumes.csv`. Tar ~10 minuter (1 188 metadata-sidor hämtas).

> **OBS:** KB:s server kräver korrekt User-Agent och Referer-header. Skriptet sätter dessa automatiskt.

### Steg 2: Ladda ner XML- och PDF-filer
```bash
python3 02_download_xml.py
```
Laddar ner 1 172 XML-filer till `xml_raw/` och 16 PDF-filer till `pdf_raw/`.
Tar ~30–60 minuter. Stöder återupptagning om det avbryts.

### Steg 3: Konvertera PDF-filer till XML
```bash
python3 04_pdf_to_xml.py
```
Extraherar text ur de 16 PDF-only volymerna (1746–1847) och sparar dem i
`pdf_raw/` med ABBYY FineReader 10-kompatibelt format.

### Steg 4: Indexera i PostgreSQL
```bash
python3 05_parse_and_index.py --reset
```
Parsar alla XML-filer, delar upp texten i sökbara chunks (~600 ord), genererar
vektorembeddings med `KBLab/sentence-bert-swedish-cased` och fyller databasen.
Normaliserar samtidigt stavningen för FTS-indexet — se avsnittet om sökning nedan.
Detta är det mest tidskrävande steget — räkna med flera timmar.

Använd `--force --volym <id>` för att tvinga omindexering av en enskild volym
(t.ex. om metadata korrigerats i `volumes.json`):

```bash
python3 05_parse_and_index.py --force --volym bih_1840-41_7_2
```

### Steg 5: Starta MCP-servern
Starta servern och anslut din MCP-klient (se konfigurationsavsnittet nedan).

---

## Konfiguration

Alla inställningar hanteras via `.env` (kopiera `config.example.env` och fyll i egna värden).

### Databasanslutning

En enda `DATABASE_URL` konfigurerar anslutningen:

```env
DATABASE_URL=postgresql://mitt_db_anvandare:losenord@localhost:5432/riksdag
```

`docker-compose.yml` läser `POSTGRES_USER`, `POSTGRES_PASSWORD` och `POSTGRES_DB`
och skapar användaren automatiskt vid första start. Se `config.example.env` för
fullständigt exempel.

### Konfiguration i MCP-klient

Servern stöder två transportlägen: **stdio** (standard) och **http** (hostad driftsättning).

#### Lokalt via stdio

Exempel med Claude Desktop — lägg till i `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "riksdagstryck": {
      "command": "/absolut/sökväg/till/.venv/bin/python",
      "args": ["/absolut/sökväg/till/kb-riksdagstryck-mcp/mcp_server.py"]
    }
  }
}
```

Andra MCP-kompatibla AI-verktyg konfigureras på motsvarande sätt — se deras dokumentation.

> Se till att PostgreSQL-containern körs (`docker compose up -d`) innan MCP-klienten startas.

#### Hostad driftsättning via HTTP

Sätt `MCP_TRANSPORT=http` i `.env`:

```bash
MCP_TRANSPORT=http python3 mcp_server.py
```

Servern lyssnar på `MCP_HOST:MCP_PORT` (standard `127.0.0.1:8000`). I produktion
läggs en reverse proxy (t.ex. Nginx) framför och hanterar TLS.

**API-nyckel:** generera och sätt i `.env`:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

Nyckeln checkas aldrig in i repot. Distribuera den separat till användare.
Klienter skickar den som `Authorization: Bearer <nyckel>`.

---

## Sökning och textåtergivning

Sökningen kombinerar PostgreSQL:s inbyggda svenska stemming (GIN-index) med
semantisk vektorsökning (HNSW-index, `KBLab/sentence-bert-swedish-cased`).
"Riksdag", "riksdagen" och "riksdagens" matchar samma sökterm.

### Historisk stavning

Texten i databasen är skriven i historisk svenska (1521–1866) med stavningsvarianter
som `hafwa`, `vtan`, `wid`, `then`, `thet`. En nutida användare som söker på moderna
former som "hava", "utan", "vid", "den", "det" hittar ändå rätt tack vare att
indexet byggs på normaliserad text.

**Originaltexten bevaras alltid orörd.** Sökresultaten visar texten precis som den
är skriven i källmaterialet — normaliseringen påverkar enbart sökindexet, inte det
som visas. Om du vill citera ur handlingarna får du alltså originaltexten.

### Query-expansion för latin och historiska synonymer (valfritt)

Handlingar från 1500–1600-talen innehåller latinska passager. Aktivera
query-expansion i `.env` för att låta ett LLM automatiskt föreslå latinska
ekvivalenter och fler historiska varianter:

```env
QUERY_EXPANSION_ENABLED=true
QUERY_EXPANSION_BASE_URL=https://api.anthropic.com/v1   # eller annan leverantör
QUERY_EXPANSION_API_KEY=din-nyckel
QUERY_EXPANSION_MODEL=claude-haiku-4-5-20251001
```

Alla OpenAI-kompatibla endpoints stöds: Claude, OpenAI, Ollama (`http://localhost:11434/v1`),
LM Studio (`http://localhost:1234/v1`) m.fl. Lämna `QUERY_EXPANSION_BASE_URL` tomt
för standard OpenAI-endpoint.

### Promptfilen — anpassa eller bygg en skill

Filen `prompts/expansion_prompt.txt` styr vad LLM:et ombeds göra. Den kan redigeras
fritt för att anpassa expansionen till ett specifikt material, en tidsperiod eller
ett ämnesdömän.

Promptfilen kan också användas som underlag för en **återanvändbar MCP-skill**: klistar
du in innehållet i en skill-definition kan vilken MCP-klient som helst anropa
query-expansion utan att servern behöver hålla koll på LLM-konfigurationen. Det
möjliggör t.ex. att olika användare av samma server använder olika LLM-backends för
sin expansion.

---

## MCP-verktyg

| Verktyg | Parametrar | Beskrivning |
|---|---|---|
| `kb_search` | `query`, `year_from`, `year_to`, `stand`, `limit` | Hybridsökning (fulltext + semantisk, viktad 35/65). Returnerar textutdrag i originalets stavning. |
| `kb_get_volume` | `volym_id` | Metadata och utdrag ur första chunken. Originalstavning. |
| `kb_list_volumes` | `year_from`, `year_to`, `stand` | Filtrerbar volymförteckning. |

---

## Databasstruktur

Tabellerna placeras i schemat `kb_riksdagstryck` för att inte krocka med övriga
arbetsströmmar i samma PostgreSQL-databas.

```sql
CREATE SCHEMA IF NOT EXISTS kb_riksdagstryck;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE kb_riksdagstryck.riksdag_chunks (
    id                    BIGSERIAL PRIMARY KEY,
    chunk_text            TEXT NOT NULL,         -- originaltext, aldrig modifierad
    chunk_text_normalized TEXT,                  -- normaliserad stavning för FTS
    volym_id              TEXT NOT NULL,
    titel                 TEXT,
    ar_fran               INTEGER,
    ar_till               INTEGER,
    stand                 TEXT,
    chunk_index           INTEGER,
    xml_url               TEXT,
    pdf_only              BOOLEAN DEFAULT FALSE,
    embedding             vector(768),
    fts_vector            tsvector GENERATED ALWAYS AS
                          (to_tsvector('swedish',
                              COALESCE(chunk_text_normalized, chunk_text))) STORED
);
```

---

## Stänga av databasen

```bash
docker compose down        # stoppar containern, data bevaras
docker compose down -v     # stoppar och raderar all data
```

---

## Källdata

- **Källa:** Kungliga biblioteket — [riksdagstryck.kb.se](https://riksdagstryck.kb.se/standsriksdagen.html)
- **Licens på data:** CC0 (public domain)
- **Format:** ABBYY FineReader 10 XML

---

## Licens

AGPL-3.0-or-later — se [LICENSE](LICENSE).
