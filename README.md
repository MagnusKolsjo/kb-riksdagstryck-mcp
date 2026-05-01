# Äldre riksdagstryck från KB (1521–1866) — MCP-server

Lokal sökbar databas över ståndsriksdagens handlingar 1521–1866, baserad på
Kungliga bibliotekets digitaliserade XML-material. Exponeras som MCP-server
till Claude Desktop.

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
Dessa konverteras till kompatibelt XML av `05_pdf_to_xml.py`.

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
# Öppna .env och byt ut lösenordet — använd samma värde som i docker-compose.yml

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
python3 05_pdf_to_xml.py
```
Extraherar text ur de 16 PDF-only volymerna (1746–1847) och sparar dem i
`xml_raw/` med ABBYY FineReader 10-kompatibelt format — samma schema som
KB:s egna XML-filer.

### Steg 4: Indexera i PostgreSQL
```bash
python3 04_parse_and_index.py
```
Parsar alla XML-filer, delar upp texten i sökbara chunks, genererar
vektorembeddings med `KBLab/sentence-bert-swedish-cased` och fyller databasen.
Modellen är tränad specifikt för semantisk likhet och körs lokalt utan API-nyckel.
Detta är det mest tidskrävande steget — räkna med flera timmar.

### Steg 5: Konfigurera Claude Desktop och starta MCP-servern
Konfigurera Claude Desktop (se nedan) och starta om programmet. MCP-servern
startas automatiskt av Claude Desktop och ansluter till PostgreSQL på
`localhost:5432`.

---

## Claude Desktop-konfiguration

Lägg till följande i `~/Library/Application Support/Claude/claude_desktop_config.json`:

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

> Se till att PostgreSQL-containern körs (`docker compose up -d`) innan
> Claude Desktop startas.

---

## MCP-verktyg

| Verktyg | Parametrar | Beskrivning |
|---|---|---|
| `kb_search` | `query`, `year_from`, `year_to`, `stand`, `limit` | Hybridsökning (fulltext + semantisk, viktad 35/65). Returnerar de bäst matchande textutdragen med poäng och källhänvisning. |
| `kb_get_volume` | `volym_id` | Metadata och utdrag ur första chunken för en specifik volym. |
| `kb_list_volumes` | `year_from`, `year_to`, `stand` | Lista indexerade volymer, filtrerbart på år och stånd. |

Sökningen kombinerar PostgreSQL:s inbyggda svenska stemming (GIN-index) med
semantisk vektorsökning (HNSW-index, `KBLab/sentence-bert-swedish-cased`).
"Riksdag", "riksdagen" och "riksdagens" matchar samma sökterm.

---

## Databasstruktur

```sql
-- Startas automatiskt av 04_parse_and_index.py
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE riksdag_chunks (
    id          BIGSERIAL PRIMARY KEY,
    chunk_text  TEXT        NOT NULL,
    volym_id    TEXT        NOT NULL,
    titel       TEXT,
    ar_fran     INTEGER,
    ar_till     INTEGER,
    stand       TEXT,
    chunk_index INTEGER,
    xml_url     TEXT,
    pdf_only    BOOLEAN DEFAULT FALSE,
    embedding   vector(768),
    fts_vector  tsvector GENERATED ALWAYS AS
                    (to_tsvector('swedish', chunk_text)) STORED
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
