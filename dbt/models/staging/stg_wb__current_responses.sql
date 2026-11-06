-- The payloads that are "current" for each logical request: all pages fetched by the most
-- recent *successful* run that fetched that request. Failed runs are ignored entirely, so a
-- partially failed refresh can never mix new and old data across indicators.

with fetches as (
    select
        f.run_id,
        f.request_key,
        f.page,
        f.response_id,
        f.fetched_at,
        r.started_at as run_started_at
    from {{ source('raw', 'api_fetch_log') }} as f
    inner join {{ source('raw', 'ingestion_run') }} as r
        on f.run_id = r.run_id
    where r.status = 'success'
),

latest_run_per_request as (
    select request_key, run_id
    from fetches
    qualify row_number() over (
        partition by request_key
        order by run_started_at desc, run_id desc
    ) = 1
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
from fetches as f
inner join latest_run_per_request as l
    on f.request_key = l.request_key
   and f.run_id = l.run_id
inner join {{ source('raw', 'api_response') }} as resp
    on f.response_id = resp.response_id
