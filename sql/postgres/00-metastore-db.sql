-- Creates the database used by Hive Metastore.
-- Runs before the other init scripts because of the 00- prefix.
-- Without this the hive-metastore container enters a crash loop because its
-- JDBC URL points at postgres:5432/metastore which does not exist by default.

CREATE DATABASE metastore;
