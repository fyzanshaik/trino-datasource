-- Inserts 1 000 000 rows into prod_sales.large using generate_series.
-- Takes ~15 s on a typical laptop postgres container.
--
-- Run with:
--   docker exec -i postgres psql -U trino -d trino < scripts/populate-large.sql

TRUNCATE TABLE prod_sales.large RESTART IDENTITY;

INSERT INTO prod_sales.large
    (val_int, val_text, val_bigint, val_decimal, val_bool, val_timestamp, val_date, val_region, val_status)
SELECT
    i,
    'row_' || i,
    i * 1000,
    round((random() * 99999)::numeric, 4),
    (i % 2 = 0),
    NOW() - (random() * INTERVAL '730 days'),
    CURRENT_DATE - (i % 730),
    (ARRAY['us-east','us-west','eu-west','eu-central','ap-south','ap-east'])[1 + (i % 6)],
    (ARRAY['active','inactive','pending','archived'])[1 + (i % 4)]
FROM generate_series(1, 1000000) AS g(i);
