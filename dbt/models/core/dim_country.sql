-- Grain: one row per country in scope (business key: country_iso3).
-- Scope comes from config; attributes from the API. A scope country the API did not describe
-- still gets a row (with NULL attributes) so facts never lose their parent - a DQ test flags it.

select
    s.country_iso3,
    c.country_iso2,
    coalesce(c.country_name, s.country_iso3)  as country_name,
    c.region_code,
    c.region_name,
    c.income_group_code,
    c.income_group_name,
    c.lending_type_code,
    c.lending_type_name,
    c.capital_city,
    c.longitude,
    c.latitude,
    s.sort_order,
    c.country_iso3 is not null                as has_source_metadata,
    c.source_response_id
from {{ source('meta', 'scope_country') }} as s
left join {{ ref('stg_wb__countries') }} as c
    on s.country_iso3 = c.country_iso3
