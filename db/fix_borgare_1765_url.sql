-- fix_borgare_1765_url.sql
-- Rättar xml_url och pdf_only för borgarståndet 1765–1766 (Del 1 och Del 2).
-- KB bekräftar: "Text i XML finns inte för denna volym" — volymerna är PDF-only.
-- PDF-URL:erna hämtades från KB:s officiella metadatasidor 2026-05-11.
--
-- Kör mot databasen som körs lokalt:
--   psql $DATABASE_URL -f fix_borgare_1765_url.sql

BEGIN;

UPDATE kb_riksdagstryck.riksdag_chunks
SET
    xml_url  = 'https://weburn.kb.se/riks/ståndsriksdagen/pdf/bg_1765-1766/bg_1765-1766_1.pdf',
    pdf_only = TRUE
WHERE volym_id = 'bg_1765-1766_1';

UPDATE kb_riksdagstryck.riksdag_chunks
SET
    xml_url  = 'https://weburn.kb.se/riks/ståndsriksdagen/pdf/bg_1765-1766/bg_1765-1766_2.pdf',
    pdf_only = TRUE
WHERE volym_id = 'bg_1765-1766_2';

-- Verifiera
SELECT volym_id, COUNT(*) AS chunk_antal, MAX(xml_url) AS xml_url, MAX(pdf_only::text) AS pdf_only
FROM kb_riksdagstryck.riksdag_chunks
WHERE volym_id IN ('bg_1765-1766_1', 'bg_1765-1766_2')
GROUP BY volym_id
ORDER BY volym_id;

COMMIT;
