-- One row per (country, indicator, year) observation returned by the API, typed and trimmed.
-- JSON null values stay NULL (never coerced to 0). If the same key appears in more than one
-- current request (e.g. overlapping scopes), the most recently fetched payload wins.

with responses as (
    select * from {{ ref('stg_wb__current_responses') }}
    where endpoint = 'observation'
),

exploded as (
    select
        response_id,
        request_url,
        page,
        fetched_in_run_id,
        fetched_at,
        source_last_updated,
        unnest(cast(json_extract(payload, '$[1]') as json[])) as obs
    from responses
),

typed as (
    select
        upper(trim(obs ->> 'countryiso3code'))     as country_iso3,
        upper(trim(obs -> 'indicator' ->> 'id'))    as indicator_code,
        try_cast(obs ->> 'date' as integer)         as year,
        try_cast(obs ->> 'value' as double)         as value,
        nullif(trim(obs ->> 'obs_status'), '')      as obs_status,
        try_cast(obs ->> 'decimal' as integer)      as decimal_places,
        nullif(trim(obs ->> 'unit'), '')            as source_unit,
        trim(obs -> 'indicator' ->> 'value')        as source_indicator_name,
        source_last_updated,
        response_id                                 as source_response_id,
        request_url                                 as source_request_url,
        page                                        as source_page,
        fetched_in_run_id                           as source_run_id,
        fetched_at                                  as source_fetched_at
    from exploded
)

select *
from typed
qualify row_number() over (
    partition by country_iso3, indicator_code, year
    order by source_fetched_at desc, source_response_id
) = 1
