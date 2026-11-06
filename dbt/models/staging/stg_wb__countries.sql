-- Country metadata, standardised: trimmed labels (the API pads some region names with
-- trailing spaces), empty strings -> NULL, coordinates typed.

with responses as (
    select * from {{ ref('stg_wb__current_responses') }}
    where endpoint = 'country'
),

exploded as (
    select
        response_id,
        fetched_at,
        unnest(cast(json_extract(payload, '$[1]') as json[])) as c
    from responses
)

select
    upper(trim(c ->> 'id'))                                   as country_iso3,
    upper(nullif(trim(c ->> 'iso2Code'), ''))                 as country_iso2,
    trim(c ->> 'name')                                        as country_name,
    nullif(trim(c -> 'region' ->> 'id'), '')                  as region_code,
    nullif(trim(c -> 'region' ->> 'value'), '')               as region_name,
    nullif(trim(c -> 'incomeLevel' ->> 'id'), '')             as income_group_code,
    nullif(trim(c -> 'incomeLevel' ->> 'value'), '')          as income_group_name,
    nullif(trim(c -> 'lendingType' ->> 'id'), '')             as lending_type_code,
    nullif(trim(c -> 'lendingType' ->> 'value'), '')          as lending_type_name,
    nullif(trim(c ->> 'capitalCity'), '')                     as capital_city,
    try_cast(nullif(trim(c ->> 'longitude'), '') as double)   as longitude,
    try_cast(nullif(trim(c ->> 'latitude'), '') as double)    as latitude,
    response_id                                               as source_response_id,
    fetched_at                                                as source_fetched_at
from exploded
qualify row_number() over (partition by country_iso3 order by fetched_at desc, response_id) = 1
