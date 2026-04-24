-- prod_marketing tables — needed for the cross_schema_view in prod_sales

CREATE TABLE prod_marketing.campaigns (
    id         BIGSERIAL PRIMARY KEY,
    name       TEXT        NOT NULL,
    channel    VARCHAR(50),
    budget     DECIMAL(12,2),
    start_date DATE,
    end_date   DATE
);

INSERT INTO prod_marketing.campaigns (name, channel, budget, start_date, end_date) VALUES
    ('Q1 Brand Awareness',  'social',  50000.00, '2026-01-01', '2026-03-31'),
    ('Spring Sale Email',    'email',   12000.00, '2026-03-15', '2026-04-15'),
    ('Paid Search – Growth', 'search',  35000.00, '2026-01-01', '2026-12-31');

CREATE TABLE prod_marketing.leads (
    id          BIGSERIAL PRIMARY KEY,
    campaign_id BIGINT REFERENCES prod_marketing.campaigns(id),
    email       VARCHAR(255),
    source      VARCHAR(50),
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
