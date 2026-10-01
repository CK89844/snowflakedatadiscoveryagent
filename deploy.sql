-- ============================================================================
-- Deploy the Data Discovery & Mapping app to Streamlit-in-Snowflake (SiS).
-- ============================================================================
-- SiS has NO outbound network, so the app auto-detects it (get_session sets
-- st.session_state["_is_sis"]) and uses the in-database mapping engine:
--   Cortex Search (grounded retrieval) -> deterministic rerank ->
--   SNOWFLAKE.CORTEX.COMPLETE reranker. No REST agent / External Access needed.
--
-- Two ways to deploy:
--   A) Snowflake CLI (recommended):  snow streamlit deploy   (uses snowflake.yml)
--   B) This script, after PUTting the files to the stage manually.
--
-- Run as a role that owns / can create in DATA_ENGINEERING_HOME.CILLIAN_TEST.
-- ============================================================================

USE ROLE DATA_ENGINEERING_HOME_PRODUCER_ROLE;
USE WAREHOUSE ENGINEERING_WH;
USE DATABASE DATA_ENGINEERING_HOME;
USE SCHEMA CILLIAN_TEST;

-- 1) Stage that holds the app files ------------------------------------------
CREATE STAGE IF NOT EXISTS STREAMLIT_STAGE
  DIRECTORY = (ENABLE = TRUE)
  COMMENT = 'Source files for the Data Discovery Streamlit app.';

-- 2) Upload the app files (skip if using `snow streamlit deploy`).
--    Run these PUTs from SnowSQL / VS Code Snowflake extension, adjusting the
--    local path. AUTO_COMPRESS=FALSE keeps the .py/.html readable in the stage.
--    PUT file://streamlit_app.py       @STREAMLIT_STAGE OVERWRITE=TRUE AUTO_COMPRESS=FALSE;
--    PUT file://cortex_agent.py        @STREAMLIT_STAGE OVERWRITE=TRUE AUTO_COMPRESS=FALSE;
--    PUT file://service_codes.py       @STREAMLIT_STAGE OVERWRITE=TRUE AUTO_COMPRESS=FALSE;
--    PUT file://environment.yml        @STREAMLIT_STAGE OVERWRITE=TRUE AUTO_COMPRESS=FALSE;
--    PUT file://docs/how_it_works.html @STREAMLIT_STAGE/docs OVERWRITE=TRUE AUTO_COMPRESS=FALSE;

-- 3) Create / replace the Streamlit object -----------------------------------
CREATE OR REPLACE STREAMLIT DATA_DISCOVERY_APP
  ROOT_LOCATION = '@DATA_ENGINEERING_HOME.CILLIAN_TEST.STREAMLIT_STAGE'
  MAIN_FILE = 'streamlit_app.py'
  QUERY_WAREHOUSE = ENGINEERING_WH
  TITLE = 'Data Discovery & Mapping';

-- 4) Privileges the app needs at runtime -------------------------------------
-- The app queries the catalogue + Cortex Search and calls CORTEX.COMPLETE, so
-- the running role needs read access to these and USAGE on Cortex.
GRANT USAGE ON DATABASE DATA_ENGINEERING_HOME  TO ROLE DATA_ENGINEERING_HOME_PRODUCER_ROLE;
GRANT USAGE ON SCHEMA   DATA_ENGINEERING_HOME.CILLIAN_TEST TO ROLE DATA_ENGINEERING_HOME_PRODUCER_ROLE;
GRANT SELECT ON TABLE   DATA_ENGINEERING_HOME.CILLIAN_TEST.CORE_COLUMN_CATALOG TO ROLE DATA_ENGINEERING_HOME_PRODUCER_ROLE;
GRANT USAGE  ON CORTEX SEARCH SERVICE DATA_ENGINEERING_HOME.CILLIAN_TEST.CORE_CATALOG_SEARCH TO ROLE DATA_ENGINEERING_HOME_PRODUCER_ROLE;
-- Run-history table (created by the app on first save); grant once it exists:
-- GRANT SELECT, INSERT ON TABLE DATA_ENGINEERING_HOME.CILLIAN_TEST.MAPPING_RUNS TO ROLE DATA_ENGINEERING_HOME_PRODUCER_ROLE;

-- 5) Share the app with viewers ----------------------------------------------
GRANT USAGE ON STREAMLIT DATA_DISCOVERY_APP TO ROLE DATA_ENGINEERING_HOME_PRODUCER_ROLE;

-- Done. Open it from Snowsight > Projects > Streamlit > DATA_DISCOVERY_APP.
