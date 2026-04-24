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

-- mv_top_customers: materialized view — Trino exposes this as a TABLE-type asset
-- via the PostgreSQL JDBC connector; tests asset-type disambiguation
CREATE MATERIALIZED VIEW prod_sales.mv_top_customers AS
SELECT customer_id, SUM(amount_cents) AS total_spent
FROM prod_sales.orders
GROUP BY customer_id;
