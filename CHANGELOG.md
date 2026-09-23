# CHANGELOG

Alla väsentliga ändringar i det här projektet dokumenteras här.
Formatet följer [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versionshanteringen följer [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

### Ändrat

- **Migrerad till `mcp` 2.x** (`MCPServer` från `mcp.server.mcpserver`, tidigare
  `FastMCP`). Egna kopior av `mcp_transport.py` och `mcp_annotationer.py` från
  projektets mallar styr uppstart och verktygsannotationer.
- **Samtliga fyra verktyg svarar strukturerat** (typat svar, `outputSchema`)
  i stället för formaterad text. `kb_search`, `kb_get_chunk`, `kb_get_volume`
  och `kb_list_volumes` är steg i samma citeringskedja — sökträffens adress
  (`volym_id` + `chunk_index`) är nu maskinläsbara fält i svaret, inte bara
  inbäddade i en textrad. Fält som kan saknas i källdata (titel, år, stånd,
  URL, antal textstycken) är `null` snarare än utelämnade.
- **Förväntade fel** (okänt volym-id, okänt chunk-index, databasfel) kastas nu
  som `ToolError` (`isError: true`) i stället för att returneras som text eller
  `{"fel": ...}`.
- **Lat inläsning av embeddingmodellen är trådsäker** — `get_encoder()` är
  skyddad av ett `threading.Lock` med dubbelkontrollerad låsning, eftersom
  synkrona verktyg i mcp 2.x kan köras samtidigt på flera arbetstrådar.
- **HTTP-transporten** använder nu den gemensamma `starta(...)`-funktionen i
  `mcp_transport.py` (Streamable HTTP). Den egna `_make_auth_app`-wrappern och
  SSE-reservvägen (`sse_app()`) är borttagna.

### Rättat

- **`kb_list_volumes` sidindelas nu.** Ett ofiltrerat anrop matchar alla
  2 447 deldokument, vilket gav ett svar (text + `structuredContent`) på
  omkring 1,25 MB — över MCP:s ~1 MB-gräns. Nya, valfria parametrar
  `max_antal` (standard 500, tak 1000) och `fran_position` sidindelar
  träffmängden; svaret bär `totalt_matchande`, `har_fler` och
  `nasta_position` för att hämta nästa sida. `year_from`, `year_to` och
  `stand` är oförändrade.
- **Läs vidare-anvisningen i `kb_get_chunk` (och de andra verktyg som delar
  `_skar_ut`) pekade på `fran_tecken + max_tecken`.** Kapningen sker på
  ord- eller radgräns, så utdraget kan bli kortare än `max_tecken` — nästa
  anrop hoppade då över det avkapade ordet. Anvisningen anger nu utdragets
  faktiska slutposition, så att på varandra följande utdrag tillsammans blir
  exakt den sammanhängande texten.

## [2.1.0] — 2026-08-10

### Tillagt

- **`kb_get_chunk(volym_id, chunk_index, kontext, max_tecken, fran_tecken)`** — nytt
  verktyg som returnerar hela textstycket bakom en sökträff, valfritt med omgivande
  stycken. `kontext=1` tar med grannarna när en mening eller paragraf löper över en
  styckegräns. Felmeddelandet skiljer okänd volym från giltig volym med okänt
  chunk-nummer och anger då hur många stycken volymen har.
- **`max_tecken`** i `kb_search` (standard `KB_MAX_TECKEN_TRAFF`, 1 500) och i
  `kb_get_volume` (standard `KB_MAX_TECKEN_UTDRAG`, 2 000). Värdet `0` stänger av
  trunkeringen. Båda finns som miljövariabler i `config.example.env`.
- **`fran_tecken`** i `kb_get_chunk` och `kb_get_volume` för att bläddra vidare i en
  text som kapats av teckentaket.
- **Adress per sökträff** — `kb_search` visar nu volymens chunk-nummer och det totala
  antalet stycken i volymen, så att en träff kan återhämtas med omgivning.

- **`instructions`-sträng på servern** — sökkontrakt, storleksregel, citatregel och
  påminnelse om begreppsexpansion till periodens terminologi. Servern saknade tidigare
  helt en beskrivning på servernivå.

### Ändrat

- **Terminologin "volym" reder ut.** KB:s katalog beskriver 1 188 bibliografiska
  volymer, men en volym är ofta uppdelad i flera filer med olika sidintervall — och
  det är de 2 447 **deldokumenten** som verktygen faktiskt arbetar på. README uppgav
  volymantalet medan `kb_list_volumes` returnerade deldokument, båda kallade
  "volymer", vilket gav motstridiga besked. Verktyget säger nu "deldokument" i sin
  utskrift, docstringen och `instructions`-strängen förklarar räknesättet, och README
  har ett eget avsnitt om skillnaden.
- **Installationen dokumenterar två likvärdiga vägar till databasen** — egen
  PostgreSQL eller den medföljande `docker-compose.yml`. Tidigare beskrevs bara
  Docker-vägen, trots att Docker är valfritt.
- `chunks` heter `textstycken` i `kb_list_volumes`-utskriften. Chunk är en
  implementationsterm; för den som läser svaret är stycke det begripliga ordet.
- **Söktermernas semantik dokumenterad.** `kb_search` sa inget om hur frågan tolkas.
  Fulltextledet behandlar hela frågan som en fras där samtliga ord måste förekomma, och
  kommatecken ignoreras — ett komma betyder alltså inte "eller" här, till skillnad från
  de nordiska servrarna. Beteendet är oförändrat; det är beskrivningen som rättats.
- **Ståndsvärdena `register` och `okant` dokumenterade** i `kb_search` och
  `kb_list_volumes`. Båda har alltid varit giltiga filtervärden men saknades i
  parameterbeskrivningarna.
- **Trunkering markeras alltid.** Textutdrag kapas på ordgräns och avslutas med en
  rad som anger hur mycket som visas av hur mycket, samt anropet som ger resten:
  `[Visar tecken 1–1 494 av 4 312. Hela textstycket: kb_get_chunk("…", 34)]`.
  Slår taket till någonstans i ett sökresultat läggs dessutom en påminnelse om att
  ett ordagrant citat aldrig får bygga på ett kapat utdrag.
- `kb_get_volume` redovisar textstyckenas nummerintervall och anger att utdraget
  kommer ur volymens första stycke — tidigare framgick inte att resten av volymen
  inte var nåbar via verktyget.

### Fixat

- **🔴 Docker-installationen kunde inte starta.** `docker-compose.yml` substituerar
  `${PGDATABASE}`, `${PGUSER}`, `${PGPASSWORD}` och `${PGPORT}` ur `.env`, men
  `config.example.env` slutade definiera dem när konfigurationen konsoliderades till
  en enda `DATABASE_URL`. Den som klonade repot, kopierade mallen och körde
  `docker compose up -d` fick tomma värden — och `POSTGRES_PASSWORD` är obligatorisk
  för postgres-imagen, så containern vägrade starta. Felet har funnits sedan första
  publiceringen. Variablerna är återinförda i mallen, i ett eget avsnitt som är
  tydligt märkt som valfritt och bara relevant för Docker-vägen.
- **Tyst trunkering i `kb_search`.** Sökträffar returnerades med `LEFT(chunk_text, 600)`
  utan markör. Eftersom ett textstycke är ~600 ord visades i praktiken bara omkring
  en sjundedel av varje träff, kapat mitt i ordet. Ordagranna citat kunde därmed inte
  verifieras: lagtexten i 18 kap. 10 § i Kungl. Maj:ts strafflagsförslag 1862–63 bröts
  vid "straffarbete från och m" och strafflatitudens övre gräns gick inte att nå med
  någon sökformulering. Fortsättningen låg i samma textstycke — den visades bara aldrig.
- Samma tysta kapning i `kb_get_volume` (`chunk_text[:800]`).

### Bakgrund

Ändringarna genomför projektets svarskontrakt (`00-las-forst.md` → "Svarskontraktet —
storlek, trunkering, adressering och sökning") i den här servern. Chunk-numret
återinförs i svarsformatet — inte som relevansdetalj, vilket var skälet till att det
togs bort i v2.0.0, utan som **adress**, tillsammans med verktyget som gör adressen
användbar.

Inga ändringar i databasschemat. Kolumnerna `chunk_index`, `char_start` och `char_end`
fanns redan men exponerades inte i MCP-lagret.

**OBS vid uppgradering:** `kb_get_chunk` är ett nytt verktyg i en befintlig server.
MCP-klienter som cachelägger verktygsindexet per servernamn kan behöva ett nytt
servernamn i konfigurationen för att se det.

---

### Ur tidigare opublicerat arbete

### Tillagt

- **`verifiera_taeckning.py`** — jämför `manifest_delar.json` mot vad som finns på
  disk och rapporterar exakt vilka deldokument som saknas. Avslutas med felkod `2`
  om något fattas, så att det inte går att av misstag gå vidare till indexeringen på
  ofullständigt underlag. Flaggar även orphaner: filer på disk utan motsvarighet i
  manifestet (oftast legitima egna PDF→XML-konverteringar, men kan vara dubbletter).
  Skriver `saknade_delar.json` som nedladdningssteget kan utgå från.

- **`--utan-index`** i `05_parse_and_index.py`: släpper de tunga indexen
  (HNSW `idx_vec` + GIN `idx_fts`, `idx_trgm`) och stänger av autovacuum på
  `riksdag_chunks` inför en stor bulkladdning. De lätta btree-indexen
  (`idx_stand`, `idx_ar`) ligger kvar. Återupptagningslogiken via
  `indexerade_volymer` är oförändrad — redan indexerade volymer hoppas över.
- **`--skapa-index`** i `05_parse_and_index.py`: fristående efterbearbetningssteg
  som bygger de tunga indexen över hela tabellen i ett svep med
  `maintenance_work_mem = 2GB` (seriellt, `max_parallel_maintenance_workers = 0`),
  slår på autovacuum igen och kör `ANALYZE`. Kör ingen indexering.
- **`manifest_delar.json`** — ny auktoritativ förteckning med en rad per
  deldokument, skapad av `01_crawl_volumes.py`. Nedladdning och verifiering utgår
  numera från den i stället för från `volumes.json`.

### Fixat

- **🔴 Systematiskt skördehål: bara ett deldokument per volym hämtades.**
  `parse_metadata_page()` i `01_crawl_volumes.py` behöll bara den *sista*
  `.xml`-länken och den *första* `.pdf`-länken per metadata-sida
  (`result["xml_url"]` skrevs över för varje träff). KB:s metadata-sidor listar
  dock flera deldokument — filstammar som slutar på `__01`, `__02`, `__03` … med
  olika sidintervall som tillsammans utgör volymen.

  Konsekvensen var att flerdelade volymer skördades ofullständigt **utan att något
  syntes**: krawlen loggade aldrig hoppade länkar, och de fullständighetskontroller
  som fanns mätte konsistens mot *de nedladdade filerna* i stället för mot KB:s
  faktiska filuppsättning. Korpusen såg därför komplett ut medan betydande delar
  saknades.

  Fixen samlar alla länkar per filstam i stället för att skriva över, avdubblar på
  filstam och filtrerar bort KB:s globala hjälptext-PDF. Efter omkrawl:
  **2 447 deldokument** (2 430 XML + 17 PDF-only) fördelade på 1 188 volymer —
  mot tidigare 1 131 nedladdade XML-filer.

  `02_download_xml.py` läser nu manifestet och är idempotent, vilket gör att samma
  körning fungerar både som förstagångsinstallation och som komplettering av en
  äldre installation. `verifiera_taeckning.py` är den permanenta spärren mot att
  felet återuppstår tyst.

### Ändrat

- `docker-compose.yml`: `shm_size: "4gb"` tillagt på postgres-tjänsten. pgvectors
  HNSW-indexbygge lägger grafen i delat minne (`/dev/shm`) dimensionerat efter
  `maintenance_work_mem`; Dockers standard på 64 MB ger annars
  `DiskFull: could not resize shared memory segment`.

### Bakgrund

Vid omindexering av den kompletterade korpusen (efter att crawlern fångat alla
deldokument) dog PostgreSQL-backenden mitt under en INSERT. Rotorsak: HNSW- och
de två GIN-indexen underhölls per insert samtidigt som insert-triggad autovacuum,
vilket överväldigade containern (`exit code 2`, ingen OOM-/signal-9-rad,
CPU-spik). Mönstret släpp-index → ladda → bygg-om eliminerar det och är dessutom
snabbare. Omindexeringen gav **311 404 chunks över 2 447 volymer, 0 utan
embedding** (tidigare 130 727 / ~1 172).

Inga ändringar i `mcp_server.py` eller databasschemat — sökverktygen och
svarsformatet är oförändrade.

---

## [2.0.2] — 2026-05-21

### Ändrat

- `mcp_server.py`: global `_conn`/`get_conn()` ersatt med per-anrops-mönstret
  `_ar_postgres()`/`_hamta_db()`/`_ph()`/`_prefix()` för konsekvens mot övriga MCP-servrar.
  `initiera_schema()` körs vid uppstart (idempotent, robust mot tillfälligt DB-bortfall).
  Alla SQL-strängar använder `{_prefix()}` i stället för hårdkodat schemaprefix.
- `05_parse_and_index.py`: `connect_db()` döpt om till `_hamta_db()`, med tillhörande
  `_ar_postgres()`, `_ph()` och `_prefix()` för konsekvens mot övriga MCP-servrar.
- `01_crawl_volumes.py` och `02_download_xml.py`: `PROJECT_UA`-konstant tillagd med
  projektets korrekta UA-sträng — redo att aktivera när KB vitlistar den.
  Befintlig Chrome-UA kvarstår som aktiv under vitlistningsperioden.

---

## [2.0.1] — 2026-05-21

### Fixat

- `config.example.env`: "fallback" → "alternativ" för SQLite-alternativet (symmetriskt arkitekturval)
- `README.md` och `05_parse_and_index.py`: "arbetsströmmar" → "MCP-servrar" i publika och interna texter
- `04_pdf_to_xml.py`: gamla filnamn i docstring rättade — `05_pdf_to_xml.py` → `04_pdf_to_xml.py`,
  `04_parse_and_index.py` → `05_parse_and_index.py` (modul-docstring, användningsexempel, log-meddelande)
- `03_inspect_xml.py`: samma filnamnsfix (rad 9 och 360)
- `.gitignore`: `*.log` och `logs/` tillagda
- `CHANGELOG.md`: `[Unreleased]`-block tillagt överst
- `README.md` (Databasstruktur): `char_start`, `char_end` och `web_dok_id` tillagda i schematabellen

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
