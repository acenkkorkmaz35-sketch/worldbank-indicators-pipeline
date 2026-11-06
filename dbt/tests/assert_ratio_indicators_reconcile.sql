-- Cross-indicator consistency: every 'ratio' indicator must reconcile with its components,
-- e.g. GDP per capita = GDP / population, within quality.gdp_per_capita_tolerance (relative).
-- Driven by dim_indicator, so a new ratio indicator added in config is checked automatically.

{% set tolerance = var('gdp_per_capita_tolerance') %}

with f as (select * from {{ ref('fct_indicator_value') }}),

checks as (
    select
        r.country_iso3,
        r.year,
        i.indicator_code                          as ratio_indicator,
        r.value                                   as reported_value,
        n.value / nullif(d.value, 0)              as derived_value
    from {{ ref('dim_indicator') }} as i
    inner join f as r on r.indicator_code = i.indicator_code
    inner join f as n
        on n.indicator_code = i.numerator_code and n.country_iso3 = r.country_iso3 and n.year = r.year
    inner join f as d
        on d.indicator_code = i.denominator_code and d.country_iso3 = r.country_iso3 and d.year = r.year
    where i.aggregation_method = 'ratio'
      and r.value is not null and n.value is not null and d.value is not null
)

select *, abs(reported_value - derived_value) / nullif(abs(reported_value), 0) as relative_diff
from checks
where derived_value is null
   or abs(reported_value - derived_value) / nullif(abs(reported_value), 0) > {{ tolerance }}
