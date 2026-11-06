{{ config(severity='warn') }}

-- Source contract (warning): the API should only return what we asked for. Rows for other
-- countries/indicators, or non-annual periods (e.g. '2020Q1' -> NULL year), indicate an API
-- behaviour change or a request-building bug.

select o.country_iso3, o.indicator_code, o.year, o.source_request_url
from {{ ref('stg_wb__observations') }} as o
where o.year is null
   or o.country_iso3 not in (select country_iso3 from {{ source('meta', 'scope_country') }})
   or o.indicator_code not in (select indicator_code from {{ source('meta', 'scope_indicator') }})
