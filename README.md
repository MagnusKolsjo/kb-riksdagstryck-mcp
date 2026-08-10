# Äldre riksdagstryck från KB (1521–1866) — MCP-server

Lokal sökbar databas över ståndsriksdagens handlingar 1521–1866, baserad på
Kungliga bibliotekets digitaliserade XML-material. Exponeras som MCP-server till
MCP-kompatibla AI-verktyg.

---

## Vad finns här?

KB har digitaliserat ståndsriksdagens handlingar till ABBYY FineReader 10 XML.
Materialet täcker alla fyra stånd — adel, präster, borgare och bönder — samt
riksdagsbeslut och bihang (bilagor).

### Två sätt att räkna — viktigt att hålla isär

KB:s katalog beskriver **1 188 bibliografiska volymer**, men en volym är ofta uppdelad
i flera filer med olika sidintervall (filstammar som slutar på `__01`, `__02`, `__03` …).
Korpusen består därför av **2 447 deldokument** — 2 430 XML och 17 PDF-only.

**Verktygen arbetar på deldokumentnivå.** Fältet `volym_id` och verktyget
`kb_list_volumes` avser deldokument, inte bibliografiska volymer. `kb_list_volumes`
returnerar alltså upp till 2 447 poster, inte 1 188. Ett `volym_id` som
`rda_1521-1560___01` är den första delen av den bibliografiska volymen
`rda_1521-1560`.

| Kategori | Bibliografiska volymer |
|---|---|
| Bihang (bilagor till protokoll) | 336 |
| Adel (ridderskapet) | 348 |
| Präster | 183 |
| Bönder | 157 |
| Borgare | 136 |
| Riksdagsbeslut | 21 |
| Register | 3 |
| Okänt (KU-handlingar 1809–1815) | 4 |

**17 deldokument** från perioden 1746–1847 (frihetstiden, Gustav III:s revolution
1772, mordet på Gustav III 1792, förlusten av Finland 1809) finns enbart som PDF
hos KB — prästeståndets och borgarståndets protokoll 1746–1766 samt två
bihangsdelar. Dessa konverteras till kompatibelt XML av `04_pdf_to_xml.py`.

> **Känd begränsning — borgarståndet 1765–1766:** KB:s digitalisering av dessa volymer
> (Del 1 och Del 2) finns inte i XML-form. KB bekräftar på sina metadatasidor:
> *"Text i XML finns inte för denna volym."* Volymerna är indexerade via extraktion
> ur KB:s sökbara PDF och källänkarna pekar på PDF-filen. Bondeståndets protokoll
> från samma riksdag (`bn_1765-1766___04`) finns i KB-native XML och är fullt sökbar.

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
```

### Steg 4: PostgreSQL med pgvector

Servern behöver en PostgreSQL-databas med `pgvector`-tillägget. Välj **en** av två
vägar — de är likvärdiga, och valet påverkar inget annat än hur databasen startas.

#### Alternativ A: egen PostgreSQL

Passar dig som redan kör PostgreSQL, eller föredrar en systeminstallation framför
containrar. Kräver PostgreSQL 14+ med `pgvector` installerat.

```bash
createdb riksdag
psql -d riksdag -c "CREATE EXTENSION IF NOT EXISTS vector;"
psql -d riksdag -c "CREATE EXTENSION IF NOT EXISTS pg_trgm;"
```

Peka `DATABASE_URL` i `.env` mot databasen. Inget mer behövs — schemat
`kb_riksdagstryck` skapas automatiskt vid första körningen. PG-variablerna i
`config.example.env` kan lämnas orörda; de används bara av alternativ B.

#### Alternativ B: PostgreSQL via Docker

Passar dig som vill komma igång utan att installera PostgreSQL. Den medföljande
`docker-compose.yml` startar `pgvector/pgvector:pg16` med rätt tillägg förinstallerade.

Fyll först i **både** `DATABASE_URL` och PG-variablerna i `.env` — Docker Compose läser
de senare för att skapa databasen och användaren, och de måste stämma överens med
`DATABASE_URL`:

```env
DATABASE_URL=postgresql://mitt_db_anvandare:mitt_losenord@localhost:5432/riksdag
PGDATABASE=riksdag
PGUSER=mitt_db_anvandare
PGPASSWORD=mitt_losenord
PGPORT=5432
```

```bash
docker compose up -d
# Vänta ~10 sekunder tills databasen är redo
```

> `POSTGRES_PASSWORD` är obligatorisk för postgres-imagen — lämnas `PGPASSWORD` tomt
> vägrar containern starta.

---

## Körning — steg för steg

### Steg 1: Kartlägg volymer från KB
```bash
python3 01_crawl_volumes.py
```
Hämtar volymförteckningen från `riksdagstryck.kb.se` och sparar tre filer:
`manifest_delar.json` (en rad per deldokument — den auktoritativa förteckningen
som steg 2 och 4 utgår från), samt `volumes.json` och `volumes.csv`.
Tar ~10 minuter; 1 157 metadata-sidor hämtas.

**En volym består ofta av flera deldokument.** KB:s metadata-sidor listar
filstammar som slutar på `__01`, `__02`, `__03` … med olika sidintervall, och
tillsammans utgör de volymen. Krawlen samlar samtliga — totalt 2 447
deldokument, varav 2 430 finns som XML och 17 bara som PDF.

> **OBS:** KB:s server kräver korrekt User-Agent och Referer-header. Skriptet sätter dessa automatiskt.

### Steg 2: Ladda ner XML- och PDF-filer
```bash
python3 02_download_xml.py
```
Läser `manifest_delar.json` och laddar ner XML för alla deldokument som har
sådan, samt PDF för de deldokument KB saknar XML för. Tar ~30–60 minuter och
stöder återupptagning om det avbryts.

Skriptet är idempotent och fungerar därför både som **förstagångsinstallation**
och som **komplettering** av en befintlig installation — allt som redan ligger
på disk hoppas över.

### Steg 2b: Verifiera täckningen
```bash
python3 verifiera_taeckning.py
```
Jämför manifestet mot vad som faktiskt finns på disk och rapporterar exakt vad
som saknas. **Kör alltid detta efter steg 2**, och särskilt efter en
komplettering av en äldre installation.

Skriptet avslutas med felkod `2` om något saknas, så att det inte går att av
misstag gå vidare till indexeringen på ofullständigt underlag. Det skriver också
`saknade_delar.json` med de deldokument som återstår att hämta.

Utöver saknade filer flaggas **orphaner** — filer på disk som inte motsvarar
något deldokument hos KB. De är oftast legitima: egna PDF→XML-konverteringar av
volymer KB bara har som PDF. Granska ändå listan så att inget är en felnedladdad
dubblett.

> **Varför spärren finns:** tidigare versioner av krawlen behöll bara *ett*
> deldokument per metadata-sida, vilket gjorde att flerdelade volymer skördades
> ofullständigt utan att något syntes i loggarna. Fullständighetskontroller som
> mäter mot de nedladdade filerna i stället för mot KB:s faktiska filuppsättning
> kan inte upptäcka den sortens hål. Den här kontrollen mäter mot manifestet.

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

> **Viktigt om återupptagning:** indexeringen hoppar över volymer som redan
> finns i `indexerade_volymer`. Det gör en avbruten körning säker att starta om,
> men innebär också att en rättning av hur metadata *härleds* — till exempel
> ståndsmappningen — inte når volymer som redan är indexerade. Efter en sådan
> ändring måste berörda volymer köras om med `--force --volym`, eller hela
> korpusen med `--reset`.

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
| `kb_search` | `query`, `year_from`, `year_to`, `stand`, `limit`, `max_tecken` | Hybridsökning (fulltext + semantisk, viktad 35/65). Returnerar textutdrag i originalets stavning, med varje träffs adress i korpusen. |
| `kb_get_chunk` | `volym_id`, `chunk_index`, `kontext`, `max_tecken`, `fran_tecken` | Hela textstycket bakom en sökträff, valfritt med omgivande stycken. |
| `kb_get_volume` | `volym_id`, `max_tecken`, `fran_tecken` | Metadata och utdrag ur volymens första textstycke. Originalstavning. |
| `kb_list_volumes` | `year_from`, `year_to`, `stand` | Filtrerbar volymförteckning. |

### Textutdrag, trunkering och ordagranna citat

Ett textstycke i databasen är ungefär 600 ord — flera tusen tecken. `kb_search`
kapar därför varje utdrag vid ett teckentak (`KB_MAX_TECKEN_TRAFF`, standard
1 500) så att ett sökresultat med många träffar inte blir oöverskådligt.

Kapningen sker alltid på ordgräns och **markeras i svaret** med hur mycket som
visas av hur mycket, tillsammans med anropet som ger resten:

```
[Visar tecken 1–1 494 av 4 312. Hela textstycket: kb_get_chunk("bih_1862-1863_1-1-1_", 34)]
```

Varje träff anger sin adress i korpusen (`Volym` + `Chunk`). Det gör det möjligt
att gå från en träff till hela texten bakom den — vilket är en förutsättning för
att kunna kontrollera att ett citat är fullständigt.

**Regel vid ordagranna citat:** återge aldrig lagtext eller protokolltext ur ett
sökutdrag som är markerat som kapat. Hämta hela stycket med `kb_get_chunk` först.
Löper meningen över en styckegräns — använd `kontext=1` för att få med grannarna.

Teckentaket kan sättas per anrop med `max_tecken` (`0` = ingen trunkering) eller
som standard i `.env`. `fran_tecken` bläddrar vidare i en text som kapats.

---

## Databasstruktur

Tabellerna placeras i schemat `kb_riksdagstryck` för att inte krocka med övriga
MCP-servrar som delar samma PostgreSQL-databas.

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
    char_start            INTEGER,               -- chunkens första teckenposition i volymens fulltext
    char_end              INTEGER,               -- chunkens sista teckenposition
    web_dok_id            INTEGER,               -- pekare till extern webbplats (NULL om sådan saknas)
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
