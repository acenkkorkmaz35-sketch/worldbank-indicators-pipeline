-- Indicator metadata as published by the API (name, definition, source organisation).

with responses as (
    select * from {{ ref('stg_wb__current_responses') }}
    where endpoint = 'indicator'
),

exploded as (
    select
        response_id,
        fetched_at,
        unnest(cast(json_extract(payload, '$[1]') as json[])) as i
    from responses
)

select
    upper(trim(i ->> 'id'))                         as indicator_code,
    trim(i ->> 'name')                              as indicator_name,
    nullif(trim(i ->> 'unit'), '')                  as source_unit,
    nullif(trim(i -> 'source' ->> 'value'), '')     as source_database,
    nullif(trim(i ->> 'sourceNote'), '')            as definition,
    nullif(trim(i ->> 'sourceOrganization'), '')    as source_organization,
    response_id                                     as source_response_id,
    fetched_at                                      as source_fetched_at
from exploded
qualify row_number() over (partition by indicator_code order by fetched_at desc, response_id) = 1
