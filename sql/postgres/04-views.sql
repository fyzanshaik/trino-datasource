-- customer_summary: exercises SHOW CREATE VIEW path in the connector
CREATE VIEW prod_sales.customer_summary AS
SELECT
    c.id,
    c.email,
    COUNT(o.order_id)                AS order_count,
    COALESCE(SUM(o.amount_cents), 0) AS total_spent
FROM prod_sales.customers c
LEFT JOIN prod_sales.orders o ON o.customer_id = c.id
GROUP BY c.id, c.email;

-- cross_schema_view: JOIN across schema boundary
CREATE VIEW prod_sales.cross_schema_view AS
SELECT c.id, c.email, p.name AS campaign_name
FROM prod_sales.customers c
CROSS JOIN prod_marketing.campaigns p;

-- order_line_enriched: composite-key join view for SHOW CREATE VIEW coverage
CREATE VIEW prod_sales.order_line_enriched AS
SELECT
    o.order_id,
    o.line_item_id,
    o.customer_id,
    i.sku,
    i.qty,
    o.amount_cents
FROM prod_sales.orders o
LEFT JOIN prod_sales.order_items i
  ON i.order_id = o.order_id
 AND i.line_id = o.line_item_id;

-- active_campaigns: cross-schema visible PostgreSQL view in a second schema
CREATE VIEW prod_marketing.active_campaigns AS
SELECT
    id,
    name,
    channel,
    budget,
    start_date,
    end_date
FROM prod_marketing.campaigns
WHERE end_date >= CURRENT_DATE;

-- Quoted view identifier in a quoted schema.
CREATE VIEW "qa-with-dash"."View With Spaces" AS
SELECT
    1 AS "Select",
    'quoted view'::text AS "label with space",
    CURRENT_DATE AS "as-of date";

-- mv_top_customers: materialized view — Trino exposes this as a TABLE-type asset
-- via the PostgreSQL JDBC connector; tests asset-type disambiguation
CREATE MATERIALIZED VIEW prod_sales.mv_top_customers AS
SELECT customer_id, SUM(amount_cents) AS total_spent
FROM prod_sales.orders
GROUP BY customer_id;
