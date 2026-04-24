-- ── prod_sales core tables ──────────────────────────────────────────────────

-- customers: single-column PK → attributes.isPrimary=true on id
CREATE TABLE prod_sales.customers (
    id           BIGINT PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    email        VARCHAR(255) NOT NULL,
    created_at   TIMESTAMPTZ,
    region       VARCHAR(50),
    tier         VARCHAR(20)   DEFAULT 'standard',
    metadata     JSONB,
    lifetime_val DECIMAL(12,2),
    is_active    BOOLEAN       NOT NULL DEFAULT TRUE,
    CONSTRAINT uq_customers_email UNIQUE (email)
);

-- orders: composite PK (order_id, line_item_id) + FK → isForeign=true on customer_id
CREATE TABLE prod_sales.orders (
    order_id      BIGINT,
    line_item_id  SMALLINT,
    customer_id   BIGINT NOT NULL REFERENCES prod_sales.customers(id),
    order_date    DATE NOT NULL,
    amount_cents  BIGINT,
    currency      CHAR(3),
    PRIMARY KEY (order_id, line_item_id)
);

-- order_items: composite FK → (order_id, line_item_id) in orders
CREATE TABLE prod_sales.order_items (
    item_id  BIGSERIAL PRIMARY KEY,
    order_id BIGINT,
    line_id  SMALLINT,
    sku      VARCHAR(64),
    qty      INTEGER,
    FOREIGN KEY (order_id, line_id) REFERENCES prod_sales.orders(order_id, line_item_id)
);

-- products: no PK, no FK (control table)
CREATE TABLE prod_sales.products (
    sku   VARCHAR(64),
    name  TEXT,
    price NUMERIC(10,2)
);

-- wide_table: 80 columns — exercises transformer batching / payload limits
-- column breakdown: 1 id + 20 vc + 15 i + 10 bi + 10 d + 8 b + 8 ts + 5 dt + 3 by = 80
CREATE TABLE prod_sales.wide_table (
    id      BIGSERIAL PRIMARY KEY,
    -- VARCHAR(255) columns
    vc_01 VARCHAR(255), vc_02 VARCHAR(255), vc_03 VARCHAR(255), vc_04 VARCHAR(255), vc_05 VARCHAR(255),
    vc_06 VARCHAR(255), vc_07 VARCHAR(255), vc_08 VARCHAR(255), vc_09 VARCHAR(255), vc_10 VARCHAR(255),
    vc_11 VARCHAR(255), vc_12 VARCHAR(255), vc_13 VARCHAR(255), vc_14 VARCHAR(255), vc_15 VARCHAR(255),
    vc_16 VARCHAR(255), vc_17 VARCHAR(255), vc_18 VARCHAR(255), vc_19 VARCHAR(255), vc_20 VARCHAR(255),
    -- INT columns
    i_01 INT, i_02 INT, i_03 INT, i_04 INT, i_05 INT,
    i_06 INT, i_07 INT, i_08 INT, i_09 INT, i_10 INT,
    i_11 INT, i_12 INT, i_13 INT, i_14 INT, i_15 INT,
    -- BIGINT columns
    bi_01 BIGINT, bi_02 BIGINT, bi_03 BIGINT, bi_04 BIGINT, bi_05 BIGINT,
    bi_06 BIGINT, bi_07 BIGINT, bi_08 BIGINT, bi_09 BIGINT, bi_10 BIGINT,
    -- DECIMAL columns
    d_01 DECIMAL(12,4), d_02 DECIMAL(12,4), d_03 DECIMAL(12,4), d_04 DECIMAL(12,4), d_05 DECIMAL(12,4),
    d_06 DECIMAL(12,4), d_07 DECIMAL(12,4), d_08 DECIMAL(12,4), d_09 DECIMAL(12,4), d_10 DECIMAL(12,4),
    -- BOOLEAN columns
    b_01 BOOLEAN, b_02 BOOLEAN, b_03 BOOLEAN, b_04 BOOLEAN,
    b_05 BOOLEAN, b_06 BOOLEAN, b_07 BOOLEAN, b_08 BOOLEAN,
    -- TIMESTAMP columns
    ts_01 TIMESTAMP, ts_02 TIMESTAMP, ts_03 TIMESTAMP, ts_04 TIMESTAMP,
    ts_05 TIMESTAMP, ts_06 TIMESTAMP, ts_07 TIMESTAMP, ts_08 TIMESTAMP,
    -- DATE columns
    dt_01 DATE, dt_02 DATE, dt_03 DATE, dt_04 DATE, dt_05 DATE,
    -- BYTEA columns
    by_01 BYTEA, by_02 BYTEA, by_03 BYTEA
);

-- reserved_keywords: column names that require quoting
CREATE TABLE prod_sales.reserved_keywords (
    "order"          INT,
    "select"         VARCHAR(255),
    "from_col"       VARCHAR(255),
    "col with space" VARCHAR(255)
);

-- commented_table: exercises REGEXP_REPLACE + SUBSTRING 100K clamp on remarks
CREATE TABLE prod_sales.commented_table (
    x INT,
    y VARCHAR(255),
    z TEXT
);
COMMENT ON TABLE prod_sales.commented_table IS 'This table tests comment extraction and sanitisation in the Atlan Trino connector. The crawler applies REGEXP_REPLACE + SUBSTRING to clamp remarks exceeding 100 000 characters. This description is intentionally verbose to exercise that code path. Padding: AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA.';
COMMENT ON COLUMN prod_sales.commented_table.x IS 'Column comment with <html>tags</html> and   multiple    spaces to exercise sanitisation';
COMMENT ON COLUMN prod_sales.commented_table.y IS 'Normal short column comment';
COMMENT ON COLUMN prod_sales.commented_table.z IS 'Another normal column comment';

-- ── Volume tables ───────────────────────────────────────────────────────────

CREATE TABLE prod_sales.empty_table (
    id        BIGSERIAL PRIMARY KEY,
    val_int   INT,
    val_text  VARCHAR(255)
);

CREATE TABLE prod_sales.tiny (
    id            BIGSERIAL PRIMARY KEY,
    val_int       INT,
    val_text      VARCHAR(255),
    val_timestamp TIMESTAMP,
    val_region    VARCHAR(50)
);
INSERT INTO prod_sales.tiny (val_int, val_text, val_timestamp, val_region)
VALUES (1, 'only-row', NOW(), 'us-east');

CREATE TABLE prod_sales.medium (
    id            BIGSERIAL PRIMARY KEY,
    val_int       INT,
    val_text      VARCHAR(255),
    val_timestamp TIMESTAMP,
    val_region    VARCHAR(50),
    val_status    VARCHAR(20)
);
INSERT INTO prod_sales.medium (val_int, val_text, val_timestamp, val_region, val_status)
SELECT
    i,
    'row_' || i,
    NOW() - (random() * INTERVAL '365 days'),
    (ARRAY['us-east','us-west','eu-west','ap-south'])[1 + (i % 4)],
    (ARRAY['active','inactive','pending'])[1 + (i % 3)]
FROM generate_series(1, 10000) AS g(i);

-- large table: populated separately via scripts/populate-large.sql (1 M rows)
CREATE TABLE prod_sales.large (
    id            BIGSERIAL PRIMARY KEY,
    val_int       INT,
    val_text      VARCHAR(255),
    val_bigint    BIGINT,
    val_decimal   DECIMAL(12,4),
    val_bool      BOOLEAN,
    val_timestamp TIMESTAMP,
    val_date      DATE,
    val_region    VARCHAR(50),
    val_status    VARCHAR(20)
);
