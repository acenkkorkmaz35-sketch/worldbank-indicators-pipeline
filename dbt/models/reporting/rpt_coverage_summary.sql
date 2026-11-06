-- Q1 (summary): which country-indicator series are incomplete?
-- Grain: one row per (country, indicator).

select
    f.country_iso3,
    c.country_name,
    f.indicator_code,
    i.short_name                                                        as indicator_short_name,
    count(*)                                                            as years_expected,
    count(*) filter (where not f.is_missing)                            as years_available,
    count(*) filter (where f.is_missing)                                as years_missing,
    round(100.0 * count(*) filter (where not f.is_missing) / count(*), 1) as completeness_pct,
    string_agg(cast(f.year as varchar), ', ' order by f.year)
        filter (where f.is_missing)                                     as missing_years,
    min(f.year) filter (where not f.is_missing)                         as first_available_year,
    max(f.year) filter (where not f.is_missing)                         as last_available_year,
    case
        when count(*) filter (where not f.is_missing) = 0        then 'empty'
        when count(*) filter (where f.is_missing) = 0            then 'complete'
        else 'incomplete'
    end                                                                 as coverage_status
from {{ ref('fct_indicator_value') }} as f
inner join {{ ref('dim_country') }} as c on f.country_iso3 = c.country_iso3
inner join {{ ref('dim_indicator') }} as i on f.indicator_code = i.indicator_code
group by all
