# CHANGELOG

Alla väsentliga ändringar i det här projektet dokumenteras här.
Formatet följer [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versionshanteringen följer [Semantic Versioning](https://semver.org/).

---

## [1.1.0] — 2026-05-03

### Tillagt
- **HTTP-transport** (`MCP_TRANSPORT=http`): servern kan nu köras som en hostad
  HTTP-tjänst bakom en reverse proxy (t.ex. `mcp.standsriksdagen.se`), utan att
  en MCP-klient behöver starta processen lokalt.
- **Bearer-token-autentisering** (`MCP_API_KEY`): när HTTP-läget är aktivt och
  en API-nyckel är satt avvisas anrop utan korrekt `Authorization`-header med
  HTTP 401.
- Nya miljövariabler i `config.example.env`: `MCP_TRANSPORT`, `MCP_HOST`,
  `MCP_PORT`, `MCP_API_KEY`.
- I HTTP-läget preladdas embedding-modellen vid uppstart (inte vid första
  sökanropet) så att svarstiden är konsekvent från start.
- `uvicorn` och `starlette` tillagda i `requirements.txt` (krävs för HTTP-läget).

### Ändrat
- Terminologi i docstring och README rättad till MCP-klientneutral formulering:
  "Claude Desktop" som generisk term ersatt med "MCP-kompatibla AI-verktyg" och
  "MCP-klient" i enlighet med projektets dokumentationsregel.
- README utökat med instruktioner för HTTP-läget och hostad driftsättning,
  inklusive nyckelhanteringsprincipen: nyckeln genereras på servern, checkas
  aldrig in i repot och distribueras separat till användare.

---

## [1.0.0] — 2026-05-01

Första publicerade versionen.

### Tillagt
- `01_crawl_volumes.py` — kartlägger 1 188 volymer från KB:s riksdagstryck
  (1 172 XML + 16 PDF-only).
- `02_download_xml.py` — laddar ner XML- och PDF-filer till `xml_raw/`
  respektive `pdf_raw/`.
- `03_inspect_xml.py` — analyserar XML-strukturen (ABBYY FineReader 10-format).
- `04_parse_and_index.py` — parsar XML, chunkar på styckegränser (~640 ord),
  genererar embeddings med `KBLab/sentence-bert-swedish-cased` och indexerar
  130 727 chunks i PostgreSQL + pgvector. Stöder återupptagning.
- `05_pdf_to_xml.py` — konverterar de 16 PDF-only volymerna (1746–1847) till
  ABBYY-kompatibel XML via PyMuPDF.
- `mcp_server.py` — MCP-server med tre verktyg:
  - `kb_search`: tvåfas hybridsökning (35 % FTS / 65 % semantisk, GIN + HNSW).
  - `kb_get_volume`: metadata och utdrag för en specifik volym.
  - `kb_list_volumes`: filtrerbar volymförteckning.
- PostgreSQL-schema med `riksdag_chunks`-tabell, GIN-index (fulltextsökning,
  svensk stemming), HNSW-index (pgvector), trigram-index (pg_trgm).
- Docker Compose-konfiguration för PostgreSQL + pgvector.
