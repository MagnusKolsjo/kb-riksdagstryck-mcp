# CHANGELOG

Alla väsentliga ändringar i det här projektet dokumenteras här.
Formatet följer [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versionshanteringen följer [Semantic Versioning](https://semver.org/).

---

## [2.0.0] — 2026-05-06

### Brytande ändringar — databas och MCP-svarsformat

**Databas-rename i schemat `kb_riksdagstryck`** — kräver migration via
`db/migration_v2_0_0.sql`. Skriptet är idempotent.

Tabeller:
- `indexed_volumes` → `indexerade_volymer`

Kolumner i `kb_riksdagstryck.indexerade_volymer`:
- `chunk_count` → `chunk_antal`
- `indexed_at` → `indexerad_vid`

**Tre nya kolumner** i `kb_riksdagstryck.riksdag_chunks` (alla NULL-default,
existerande chunks påverkas inte):
- `char_start INTEGER` — chunkens första teckenposition i volymens fulltext
- `char_end INTEGER` — chunkens sista teckenposition
- `web_dok_id INTEGER` — pekare till motsvarande dokument i en framtida
  webbplats-tabell (riksdagstryck_web-schema, byggs i ström 7). Inget
  FK-constraint i denna release.

`char_start` / `char_end` populeras vid framtida (re-)indexering med
uppdaterad chunknings-pipeline. Befintliga chunks behåller NULL.

**MCP-svarsformat — `kb_search`:**
- Borttag av raden `Chunk: N` (implementationsdetalj utan värde för användaren)
- Borttag av raden `Poäng: X.X (FTS: Y.Y, Semantisk: Z.Z)` (intern för rankning)
- Påverkar parsade svar — klienter som tolkade specifika rader behöver uppdateras

**Python-identifierare** — 2 unika identifierare med å/ä/ö → ASCII-svenska:
- `mönster` → `monster`
- `ersättning` → `ersattning`

(Båda i lokal `_NORM_REGLER`-loop i `05_parse_and_index.py`.)

### Tekniskt

- Ny `db/migration_v2_0_0.sql` med PL/pgSQL-helperfunktioner
  `pg_temp.byt_tabell`, `pg_temp.byt_kolumn` och `pg_temp.lagg_till_kolumn`.

---

## [1.2.1] — 2026-05-03

### Fixat
- **Bugg i kb_search med årsfilter**: parametrarna till vec_hits-CTEn var i fel ordning.
  params (år/stånd-filter) skickades in före vec_literal, vilket orsakade ett typfel i
  PostgreSQL när year_from, year_to eller stand användes. Rätt ordning:
  [vec_literal] + params + [vec_literal] (buggfix i full_params-konstruktionen).

---

## [1.2.0] — 2026-05-03

### Tillagt
- **Omdöpning av skript**: `04_parse_and_index.py` → `05_parse_and_index.py` och
  `05_pdf_to_xml.py` → `04_pdf_to_xml.py` för att spegla faktisk körordning
  (PDF-konvertering måste köras före indexering).
- **`--force`-flagga** i `05_parse_and_index.py`: möjliggör omindexering av en
  enskild volym (`--force --volym <id>`) utan att återskapa hela databasen. Raderar
  befintliga chunks för volymen och indexerar om från XML-filen.
- **Inferens av metadata ur volym_id**: om en XML-fil saknar post i `volumes.json`
  loggas nu en tydlig `WARNING` och stånd/år härledas automatiskt ur filnamnsprefixet
  (`bih_` → bihang, `pr_` → praster, `roa_`/`rda_` → adel, `bg_` → borgare,
  `bn_` → bönder m.fl.). Förhindrar tysta `NULL`-värden i databasen vid framtida
  körningar.
- `volumes.json` kompletterad med `bih_1840-41_7_2` och `bih_1847-48_7_2`
  (stand=bihang, korrekt årsintervall) — dessa PDF-only volymer saknades sedan
  den initiala krälningen.
- **Stavningsnormalisering** vid indexering: originaltexten i `chunk_text`
  bevaras orörd, men `fts_vector` byggs nu från en normaliserad kopia
  (`chunk_text_normalized`). Moderna söktermer (t.ex. "hava", "utan", "efter")
  hittar nu text med historisk stavning (t.ex. "hafwa", "vtan", "effter").
  Reglerna täcker de vanligaste grafematiska variationerna 1521–1866 och är
  lätta att utöka i `_NORM_REGLER`.
- **Query-expansion** (valfritt): `mcp_server.py` kan utöka söktermen med
  historiska stavningsvarianter och latinska ekvivalenter via ett externt
  LLM-anrop. Aktiveras med `QUERY_EXPANSION_ENABLED=true`. Stöder alla
  OpenAI-kompatibla endpoints (Claude, OpenAI, Ollama, LM Studio m.fl.).
- Ny underkatalog `prompts/` med `expansion_prompt.txt` — promptfilen styr
  expansionsbeteendet och kan redigeras fritt. Den kan också användas som
  underlag för en återanvändbar MCP-skill; se README för detaljer.
- Ny kolumn `chunk_text_normalized TEXT` i `riksdag_chunks`.

### Ändrat
- **DATABASE_URL**: de fem separata miljövariablerna `PGHOST`, `PGPORT`,
  `PGDATABASE`, `PGUSER` och `PGPASSWORD` är ersatta med en enda `DATABASE_URL`
  i alla Python-filer och i `config.example.env`. Format:
  `postgresql://anvandare:losenord@localhost:5432/riksdag`.
- **PostgreSQL-schema**: alla tabeller är nu prefix-ade med schemat
  `kb_riksdagstryck` (`kb_riksdagstryck.riksdag_chunks`,
  `kb_riksdagstryck.indexed_volumes`). Schemat skapas automatiskt vid
  uppstart och isolerar tabellerna från övriga arbetsströmmar som delar
  samma databas.
- `fts_vector` genereras nu från `chunk_text_normalized` i stället för
  `chunk_text` (med COALESCE-fallback om normalisering saknas).

### Tekniska noter
- Omindexering med `--reset` krävs för att det nya schemat och
  `chunk_text_normalized` ska gälla befintlig data.
- `openai`-paketet tillkommer i `requirements.txt` (behövs bara om
  `QUERY_EXPANSION_ENABLED=true`).

---

## [1.1.0] — 2026-05-03

### Tillagt
- **HTTP-transport** (`MCP_TRANSPORT=http`): servern kan nu köras som en hostad
  HTTP-tjänst bakom en reverse proxy (t.ex. `mcp.standsriksdagen.se`).
- **Bearer-token-autentisering** (`MCP_API_KEY`): anrop utan korrekt
  `Authorization`-header avvisas med HTTP 401.
- Nya miljövariabler: `MCP_TRANSPORT`, `MCP_HOST`, `MCP_PORT`, `MCP_API_KEY`.
- I HTTP-läget preladdas embedding-modellen vid uppstart.
- `uvicorn` och `starlette` tillagda i `requirements.txt`.

### Ändrat
- Terminologi rättad till MCP-klientneutral formulering i docstrings och README.
- README utökat med instruktioner för HTTP-läget och hostad driftsättning.

---

## [1.0.0] — 2026-05-01

Första publicerade versionen.

### Tillagt
- `01_crawl_volumes.py` — kartlägger 1 188 volymer från KB:s riksdagstryck.
- `02_download_xml.py` — laddar ner XML- och PDF-filer.
- `03_inspect_xml.py` — analyserar XML-strukturen (ABBYY FineReader 10).
- `05_parse_and_index.py` — parsar XML, chunkar, genererar embeddings,
  indexerar 130 727 chunks i PostgreSQL + pgvector.
- `04_pdf_to_xml.py` — konverterar de 16 PDF-only volymerna (1746–1847).
- `mcp_server.py` — MCP-server med `kb_search`, `kb_get_volume`, `kb_list_volumes`.
- PostgreSQL-schema med GIN-, HNSW- och trigram-index.
- Docker Compose-konfiguration för PostgreSQL + pgvector.
