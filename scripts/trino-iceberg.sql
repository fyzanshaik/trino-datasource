-- Run against the no-auth Trino endpoint after the stack is healthy.
-- docker exec -i trino trino < scripts/trino-iceberg.sql

-- ── Schemas ─────────────────────────────────────────────────────────────────

CREATE SCHEMA IF NOT EXISTS iceberg.main
    WITH (location = 's3a://iceberg/main/');

CREATE SCHEMA IF NOT EXISTS iceberg.curated
    WITH (location = 's3a://iceberg/curated/');

CREATE SCHEMA IF NOT EXISTS iceberg.scratch
    WITH (location = 's3a://iceberg/scratch/');

-- ── dim_customer: month() transform partition — tests iceberg partition detection

CREATE TABLE IF NOT EXISTS iceberg.curated.dim_customer (
    customer_id     BIGINT,
    name            VARCHAR,
    registered_date DATE
) WITH (
    location     = 's3a://iceberg/curated/dim_customer/',
    partitioning = ARRAY['month(registered_date)']
);

INSERT INTO iceberg.curated.dim_customer
SELECT *
FROM (
    VALUES
        (1,  'Alice Chen',   DATE '2025-01-15'),
        (2,  'Bob Patel',    DATE '2025-02-20'),
        (3,  'Carol Smith',  DATE '2025-03-05'),
        (4,  'David Kim',    DATE '2025-04-12'),
        (5,  'Eva Torres',   DATE '2025-05-28'),
        (6,  'Frank Nguyen', DATE '2026-01-03'),
        (7,  'Grace Obi',    DATE '2026-02-14'),
        (8,  'Henry Liu',    DATE '2026-03-22')
) AS t(customer_id, name, registered_date)
WHERE NOT EXISTS (SELECT 1 FROM iceberg.curated.dim_customer);
