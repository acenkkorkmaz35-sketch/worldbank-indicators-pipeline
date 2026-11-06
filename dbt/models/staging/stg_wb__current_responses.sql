-- The payloads that make up the current view of the source: every page fetched by the most
-- recent *successful* ingestion run.
--
-- Why the whole run, not "latest fetch per request":
--   * one run = one consistent snapshot of the source (GDP, population and GDP per capita
--     all come from the same moment, so cross-indicator checks are meaningful);
--   * a value the source withdraws, or a country removed from scope, can never be resurrected
--     from an older request whose key no longer exists (e.g. a different country list);
--   * failed or still-running runs are ignored entirely, so a refresh that dies half way never
--     mixes new and old data.
-- Every run fetches the full configured scope, so the latest successful run is complete.
-- (A partial-refresh mode would need per-request selection - see README "next steps".)

with latest_successful_run as (
    select run_id
    from {{ source('raw', 'ingestion_run') }}
    where status = 'success'
    qualify row_number() over (order by started_at desc, run_id desc) = 1
)

select
    resp.response_id,
    resp.request_key,
    resp.endpoint,
    resp.entity,
    resp.request_url,
    resp.page,
    resp.pages,
    resp.total,
    resp.source_last_updated,
    resp.payload,
    f.run_id      as fetched_in_run_id,
    f.fetched_at
from {{ source('raw', 'api_fetch_log') }} as f
inner join latest_successful_run as r
    on f.run_id = r.run_id
inner join {{ source('raw', 'api_response') }} as resp
    on f.response_id = resp.response_id
