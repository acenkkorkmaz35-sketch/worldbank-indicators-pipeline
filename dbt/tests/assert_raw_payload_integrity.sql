-- Raw-layer integrity: payloads are stored byte-for-byte and are what their ids claim.
--   * payload_sha256 must equal sha256 of the stored body (nothing altered after landing);
--   * every successful run must have fetched every request page it logged (no gaps 1..pages).

with tampered as (
    select response_id, 'payload hash mismatch' as problem
    from {{ source('raw', 'api_response') }}
    where sha256(cast(payload as varchar)) <> payload_sha256
),

pages_per_request as (
    select f.run_id, f.request_key, count(*) as pages_logged, max(r.pages) as pages_expected
    from {{ source('raw', 'api_fetch_log') }} as f
    inner join {{ source('raw', 'api_response') }} as r on f.response_id = r.response_id
    inner join {{ source('raw', 'ingestion_run') }} as run on f.run_id = run.run_id
    where run.status = 'success'
    group by f.run_id, f.request_key
),

incomplete as (
    select run_id || ' ' || request_key as response_id, 'request pages missing in run' as problem
    from pages_per_request
    where pages_logged <> pages_expected
)

select * from tampered
union all
select * from incomplete
