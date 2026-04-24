-- Run against the no-auth Trino endpoint after the stack is healthy.
-- docker exec -i trino trino < scripts/trino-hive.sql

-- ── Schemas ─────────────────────────────────────────────────────────────────

CREATE SCHEMA IF NOT EXISTS hive.default   WITH (location = 's3a://hive/default/');
CREATE SCHEMA IF NOT EXISTS hive.warehouse WITH (location = 's3a://hive/warehouse/');
CREATE SCHEMA IF NOT EXISTS hive.events_raw     WITH (location = 's3a://hive/events_raw/');
CREATE SCHEMA IF NOT EXISTS hive.events_staging WITH (location = 's3a://hive/events_staging/');

-- ── page_views: single-column partition ─────────────────────────────────────
-- Partition columns must appear at the END of the column list;
-- partitioned_by takes column names only (no type).

CREATE TABLE IF NOT EXISTS hive.events_raw.page_views (
    user_id   BIGINT,
    page_url  VARCHAR,
    viewed_at TIMESTAMP,
    dt        VARCHAR
) WITH (
    format            = 'PARQUET',
    external_location = 's3a://hive/events_raw/page_views/',
    partitioned_by    = ARRAY['dt']
);

INSERT INTO hive.events_raw.page_views VALUES (1,  '/home',     TIMESTAMP '2026-04-01 10:00:00', '2026-04-01');
INSERT INTO hive.events_raw.page_views VALUES (2,  '/products', TIMESTAMP '2026-04-02 11:00:00', '2026-04-02');
INSERT INTO hive.events_raw.page_views VALUES (3,  '/cart',     TIMESTAMP '2026-04-03 12:00:00', '2026-04-03');
INSERT INTO hive.events_raw.page_views VALUES (4,  '/checkout', TIMESTAMP '2026-04-04 13:00:00', '2026-04-04');
INSERT INTO hive.events_raw.page_views VALUES (5,  '/home',     TIMESTAMP '2026-04-05 10:00:00', '2026-04-05');
INSERT INTO hive.events_raw.page_views VALUES (6,  '/search',   TIMESTAMP '2026-04-06 09:00:00', '2026-04-06');
INSERT INTO hive.events_raw.page_views VALUES (7,  '/products', TIMESTAMP '2026-04-07 14:00:00', '2026-04-07');
INSERT INTO hive.events_raw.page_views VALUES (8,  '/about',    TIMESTAMP '2026-04-08 15:00:00', '2026-04-08');
INSERT INTO hive.events_raw.page_views VALUES (9,  '/contact',  TIMESTAMP '2026-04-09 16:00:00', '2026-04-09');
INSERT INTO hive.events_raw.page_views VALUES (10, '/home',     TIMESTAMP '2026-04-10 10:00:00', '2026-04-10');

-- ── clickstream: multi-column partition ──────────────────────────────────────

CREATE TABLE IF NOT EXISTS hive.events_raw.clickstream (
    user_id    BIGINT,
    event_type VARCHAR,
    payload    VARCHAR,
    dt         VARCHAR,
    region     VARCHAR
) WITH (
    format            = 'PARQUET',
    external_location = 's3a://hive/events_raw/clickstream/',
    partitioned_by    = ARRAY['dt', 'region']
);

INSERT INTO hive.events_raw.clickstream VALUES (1, 'click',  '{"btn":"buy"}',    '2026-04-01', 'us-east');
INSERT INTO hive.events_raw.clickstream VALUES (2, 'view',   '{"page":"/"}',     '2026-04-01', 'eu-west');
INSERT INTO hive.events_raw.clickstream VALUES (3, 'search', '{"q":"widget"}',   '2026-04-02', 'us-east');
INSERT INTO hive.events_raw.clickstream VALUES (4, 'click',  '{"btn":"cart"}',   '2026-04-02', 'eu-west');
INSERT INTO hive.events_raw.clickstream VALUES (5, 'view',   '{"page":"/sale"}', '2026-04-03', 'ap-south');
INSERT INTO hive.events_raw.clickstream VALUES (6, 'search', '{"q":"offer"}',    '2026-04-03', 'us-east');

-- ── orders_snapshot: non-partitioned table in Hive catalog ──────────────────
-- Confirms is_partitioned=None / field absent from JSON output (#11 behavior)

CREATE TABLE IF NOT EXISTS hive.warehouse.orders_snapshot (
    order_id    BIGINT,
    customer_id BIGINT,
    total_cents BIGINT,
    snapshot_dt DATE
) WITH (
    format            = 'PARQUET',
    external_location = 's3a://hive/warehouse/orders_snapshot/'
);
