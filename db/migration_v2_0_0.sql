-- ============================================================
-- stream-02-kb-riksdagstryck: Migration v2.0.0 — ASCII-svenska + nya kolumner
-- ============================================================
-- Brytande migration. Tre samordnade ändringar:
--
-- 1. Tabellrenamn: kb_riksdagstryck.indexed_volumes → indexerade_volymer
-- 2. Kolumnrenamn: chunk_count → chunk_antal, indexed_at → indexerad_vid
-- 3. Kolumntillägg: char_start, char_end, web_dok_id i riksdag_chunks
--    (alla NULL-default, inget tvång att populera retroaktivt)
--
-- Idempotent. Säker att köra om.
-- Förutsättning: Claude Desktop ska vara stängt.
-- ============================================================

\set ON_ERROR_STOP on

BEGIN;

-- ----------------------------------------------------------
-- Hjälpfunktioner
-- ----------------------------------------------------------

CREATE OR REPLACE FUNCTION pg_temp.byt_tabell(
    p_schema TEXT, p_gammal TEXT, p_ny TEXT
) RETURNS VOID AS $func$
DECLARE
    fg BOOLEAN; fn BOOLEAN;
BEGIN
    SELECT EXISTS(SELECT 1 FROM information_schema.tables
        WHERE table_schema = p_schema AND table_name = p_gammal) INTO fg;
    SELECT EXISTS(SELECT 1 FROM information_schema.tables
        WHERE table_schema = p_schema AND table_name = p_ny) INTO fn;
    IF fg AND NOT fn THEN
        EXECUTE format('ALTER TABLE %I.%I RENAME TO %I', p_schema, p_gammal, p_ny);
        RAISE NOTICE 'Bytte tabell %.% -> %', p_schema, p_gammal, p_ny;
    ELSIF fn AND NOT fg THEN
        RAISE NOTICE 'Tabell %.% -> % redan applicerad — hoppar', p_schema, p_gammal, p_ny;
    ELSIF NOT fn AND NOT fg THEN
        RAISE EXCEPTION 'Varken tabell % eller % finns i schema %', p_gammal, p_ny, p_schema;
    ELSE
        RAISE EXCEPTION 'BÅDA tabellerna % och % finns i %', p_gammal, p_ny, p_schema;
    END IF;
END;
$func$ LANGUAGE plpgsql;


CREATE OR REPLACE FUNCTION pg_temp.byt_kolumn(
    p_schema TEXT, p_tabell TEXT, p_gammal TEXT, p_ny TEXT
) RETURNS VOID AS $func$
DECLARE
    fg BOOLEAN; fn BOOLEAN;
BEGIN
    SELECT EXISTS(SELECT 1 FROM information_schema.columns
        WHERE table_schema = p_schema AND table_name = p_tabell AND column_name = p_gammal) INTO fg;
    SELECT EXISTS(SELECT 1 FROM information_schema.columns
        WHERE table_schema = p_schema AND table_name = p_tabell AND column_name = p_ny) INTO fn;
    IF fg AND NOT fn THEN
        EXECUTE format('ALTER TABLE %I.%I RENAME COLUMN %I TO %I',
                       p_schema, p_tabell, p_gammal, p_ny);
        RAISE NOTICE 'Bytte kolumn %.%.% -> %', p_schema, p_tabell, p_gammal, p_ny;
    ELSIF fn AND NOT fg THEN
        RAISE NOTICE 'Kolumn %.%.% -> % redan applicerad — hoppar', p_schema, p_tabell, p_gammal, p_ny;
    ELSIF NOT fn AND NOT fg THEN
        RAISE EXCEPTION 'Varken kolumn % eller % finns i %.%', p_gammal, p_ny, p_schema, p_tabell;
    ELSE
        RAISE EXCEPTION 'BÅDA kolumnerna % och % finns i %.%', p_gammal, p_ny, p_schema, p_tabell;
    END IF;
END;
$func$ LANGUAGE plpgsql;


CREATE OR REPLACE FUNCTION pg_temp.lagg_till_kolumn(
    p_schema TEXT, p_tabell TEXT, p_kolumn TEXT, p_typ TEXT
) RETURNS VOID AS $func$
DECLARE
    finns BOOLEAN;
BEGIN
    SELECT EXISTS(SELECT 1 FROM information_schema.columns
        WHERE table_schema = p_schema AND table_name = p_tabell AND column_name = p_kolumn) INTO finns;
    IF finns THEN
        RAISE NOTICE 'Kolumn %.%.% finns redan — hoppar', p_schema, p_tabell, p_kolumn;
    ELSE
        EXECUTE format('ALTER TABLE %I.%I ADD COLUMN %I %s', p_schema, p_tabell, p_kolumn, p_typ);
        RAISE NOTICE 'Tilladd kolumn %.%.% (%)', p_schema, p_tabell, p_kolumn, p_typ;
    END IF;
END;
$func$ LANGUAGE plpgsql;


-- ----------------------------------------------------------
-- 1) Tabellrenamn
-- ----------------------------------------------------------
SELECT pg_temp.byt_tabell('kb_riksdagstryck', 'indexed_volumes', 'indexerade_volymer');

-- ----------------------------------------------------------
-- 2) Kolumnrenamn
-- ----------------------------------------------------------
SELECT pg_temp.byt_kolumn('kb_riksdagstryck', 'indexerade_volymer', 'chunk_count', 'chunk_antal');
SELECT pg_temp.byt_kolumn('kb_riksdagstryck', 'indexerade_volymer', 'indexed_at',  'indexerad_vid');

-- ----------------------------------------------------------
-- 3) Kolumntillägg i riksdag_chunks (för framtida bruk)
-- ----------------------------------------------------------
-- char_start / char_end: chunkens teckenposition i volymens fulltext.
-- Populeras vid framtida indexering (uppdaterad chunknings-pipeline).
-- Existerande chunks får NULL — påverkar inte sökning eller funktion.
SELECT pg_temp.lagg_till_kolumn('kb_riksdagstryck', 'riksdag_chunks', 'char_start', 'INTEGER');
SELECT pg_temp.lagg_till_kolumn('kb_riksdagstryck', 'riksdag_chunks', 'char_end',   'INTEGER');

-- web_dok_id: pekare till motsvarande dokument i ström 7:s riksdagstryck_web-schema.
-- Populeras enbart av den som även hostar webbplatsen (ström 7) — alla andra
-- användare påverkas inte. Inget FK-constraint nu eftersom riksdagstryck_web-tabellerna
-- inte är skapade än; constraint kan adderas senare när ström 7 är på plats.
SELECT pg_temp.lagg_till_kolumn('kb_riksdagstryck', 'riksdag_chunks', 'web_dok_id', 'INTEGER');

COMMIT;

-- Efterkontroll:
-- \dt kb_riksdagstryck.*       ska visa: riksdag_chunks, indexerade_volymer
-- \d kb_riksdagstryck.indexerade_volymer  ska visa kolumner: volym_id, chunk_antal, indexerad_vid
-- \d kb_riksdagstryck.riksdag_chunks      ska bl.a. visa: char_start, char_end, web_dok_id
