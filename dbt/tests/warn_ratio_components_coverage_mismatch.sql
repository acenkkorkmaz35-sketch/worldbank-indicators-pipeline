{{ config(severity='warn') }}

-- Cross-indicator coverage (warning): a ratio indicator and its components should be available
-- for the same country-years. A per-capita value without GDP or population (or vice versa)
-- usually means one series was revised or published before the other.

with f as (select * from {{ ref('fct_indicator_value') }})

select
    r.country_iso3,
    r.year,
    i.indicator_code   as ratio_indicator,
    r.is_missing       as ratio_missing,
    n.is_missing       as numerator_missing,
    d.is_missing       as denominator_missing
from {{ ref('dim_indicator') }} as i
inner join f as r on r.indicator_code = i.indicator_code
inner join f as n
    on n.indicator_code = i.numerator_code and n.country_iso3 = r.country_iso3 and n.year = r.year
inner join f as d
    on d.indicator_code = i.denominator_code and d.country_iso3 = r.country_iso3 and d.year = r.year
where i.aggregation_method = 'ratio'
  and (r.is_missing <> n.is_missing or r.is_missing <> d.is_missing)
