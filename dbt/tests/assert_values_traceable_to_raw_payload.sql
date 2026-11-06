-- Traceability: every value in the fact table must be reproducible from the exact raw payload
-- it points to. Re-parses the referenced raw.api_response and compares value by value.

with fact as (
    select * from {{ ref('fct_indicator_value') }}
    where source_response_id is not null
),

raw_rows as (
    select
        r.response_id,
        r.request_url,
        unnest(cast(json_extract(r.payload, '$[1]') as json[])) as obs
    from {{ source('raw', 'api_response') }} as r
    where r.response_id in (select source_response_id from fact)
),

raw_values as (
    select
        response_id,
        request_url,
        upper(trim(obs ->> 'countryiso3code'))   as country_iso3,
        upper(trim(obs -> 'indicator' ->> 'id'))  as indicator_code,
        try_cast(obs ->> 'date' as integer)       as year,
        try_cast(obs ->> 'value' as double)       as value
    from raw_rows
)

select f.country_iso3, f.indicator_code, f.year, f.value as fact_value, r.value as raw_value,
       f.source_response_id, r.response_id as matched_response_id
from fact as f
left join raw_values as r
    on r.response_id = f.source_response_id
   and r.country_iso3 = f.country_iso3
   and r.indicator_code = f.indicator_code
   and r.year = f.year
where r.response_id is null                         -- pointer leads nowhere
   or f.value is distinct from r.value              -- value differs from the payload
   or f.source_request_url <> r.request_url         -- URL lineage inconsistent
