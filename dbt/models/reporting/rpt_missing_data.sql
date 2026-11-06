-- Q1 (row level): which country-indicator-year combinations are missing?
-- Grain: one row per missing (country, indicator, year).
-- gap_type separates "not published yet" (after the last year the country has data for -
-- typically publication lag) from a genuine hole in the series.

with f as (
    select
        *,
        max(case when not is_missing then year end)
            over (partition by country_iso3, indicator_code) as last_available_year,
        min(case when not is_missing then year end)
            over (partition by country_iso3, indicator_code) as first_available_year
    from {{ ref('fct_indicator_value') }}
)

select
    f.country_iso3,
    c.country_name,
    c.income_group_name,
    f.indicator_code,
    i.short_name           as indicator_short_name,
    f.year,
    f.missing_reason,
    case
        when f.last_available_year is null       then 'no_data_in_scope'
        when f.year > f.last_available_year      then 'not_yet_published'
        when f.year < f.first_available_year     then 'before_series_start'
        else 'gap_in_series'
    end                    as gap_type,
    f.last_available_year,
    f.source_request_url   as checked_request_url
from f
inner join {{ ref('dim_country') }} as c on f.country_iso3 = c.country_iso3
inner join {{ ref('dim_indicator') }} as i on f.indicator_code = i.indicator_code
where f.is_missing
