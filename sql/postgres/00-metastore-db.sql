-- Provision the metastore database. The Hive Metastore container creates its
-- own schema on first start.
CREATE DATABASE metastore;
GRANT ALL PRIVILEGES ON DATABASE metastore TO trino;
