-- Generates 20 bulk schemas × 500 tables × 51 columns = ~510 000 column assets.
-- Run AFTER the postgres container is healthy (not in docker-entrypoint-initdb.d
-- because 10 000 CREATE TABLE statements take ~5 min and would delay the healthcheck).
--
-- Run with:
--   docker exec -i postgres psql -U trino -d trino < scripts/bulk-generate.sql
--
-- Asset count produced by this script alone:
--   Schemas : 20
--   Tables  : 10 000
--   Columns : 510 000  (51 cols × 10 000 tables)
--   Total   : 520 020

DO $$
DECLARE
  s    INT;
  t    INT;
  cols TEXT;
  i    INT;
BEGIN
  FOR s IN 1..20 LOOP
    EXECUTE format('CREATE SCHEMA IF NOT EXISTS bulk_%s', lpad(s::text, 3, '0'));

    FOR t IN 1..500 LOOP
      -- 51 columns: id + 10 varchar + 10 int + 10 decimal + 10 timestamp + 10 boolean
      cols := 'id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY';
      FOR i IN 1..10 LOOP
        cols := cols || format(', vc_%s VARCHAR(128)', lpad(i::text, 2, '0'));
      END LOOP;
      FOR i IN 1..10 LOOP
        cols := cols || format(', n_%s  INT',          lpad(i::text, 2, '0'));
      END LOOP;
      FOR i IN 1..10 LOOP
        cols := cols || format(', d_%s  DECIMAL(12,4)', lpad(i::text, 2, '0'));
      END LOOP;
      FOR i IN 1..10 LOOP
        cols := cols || format(', ts_%s TIMESTAMP',    lpad(i::text, 2, '0'));
      END LOOP;
      FOR i IN 1..10 LOOP
        cols := cols || format(', b_%s  BOOLEAN',      lpad(i::text, 2, '0'));
      END LOOP;

      EXECUTE format(
        'CREATE TABLE IF NOT EXISTS bulk_%s.tbl_%s (%s)',
        lpad(s::text, 3, '0'),
        lpad(t::text, 4, '0'),
        cols
      );
    END LOOP;

    RAISE NOTICE 'bulk_% done', lpad(s::text, 3, '0');
  END LOOP;
END $$;
