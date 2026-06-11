-- Postgres init script for the SSO topology compose (docker-compose.sso.yml).
-- Creates the two extra databases that inferia-auth needs (the InferiaLLM
-- gateway DB is created by the POSTGRES_DB env var on the postgres service).
--
-- Safe to re-run: CREATE DATABASE is idempotent inside the docker-entrypoint
-- shim because the init scripts only run on the FIRST boot of a fresh data
-- volume. To force a re-run, `docker compose down -v` to drop sso-pgdata.

CREATE DATABASE inferia_auth;
CREATE DATABASE inferia_openfga;
