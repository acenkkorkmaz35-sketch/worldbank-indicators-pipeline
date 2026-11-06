-- Q2: year-over-year change per country and indicator.
-- Grain: one row per (country, indicator, year) in scope.
--
-- Rules
--   * Strict YoY compares with year - 1 only. The fact table is a complete spine, so LAG over
--     year is always the previous calendar year.
--   * pct_change is NULL (never 0 or infinity) when the prior value is missing or zero;
--     yoy_status says why. abs_change is still given when the prior value is zero.
--   * Denominator is abs(prior) so the sign of pct_change follows the direction of change.
--   * For gap-tolerant analysis, *_vs_last_available compares with the most recent earlier year
--     that has a value, annualised (CAGR) over the gap.

with f as (
    select
        country_iso3,
        indicator_code,
        year,
        value,
        lag(value) over w                                      as prior_year_value,
        source_response_id,
        lag(source_response_id) over w                         as prior_source_response_id,
        last_value(value ignore nulls) over w_prev             as last_available_prior_value,
        last_value(case when value is not null then year end ignore nulls) over w_prev
                                                               as last_available_prior_year,
        min(year) over (partition by country_iso3, indicator_code) as first_year_in_scope
    from {{ ref('fct_indicator_value') }}
    window
        w as (partition by country_iso3, indicator_code order by year),
        w_prev as (partition by country_iso3, indicator_code order by year
                   rows between unbounded preceding and 1 preceding)
)

select
    f.country_iso3,
    c.country_name,
    f.indicator_code,
    i.short_name                                       as indicator_short_name,
    i.unit,
    f.year,
    f.value,
    f.prior_year_value,
    case when f.value is not null and f.prior_year_value is not null
         then f.value - f.prior_year_value end         as abs_change,
    case when f.value is not null and f.prior_year_value is not null and f.prior_year_value <> 0
         then (f.value - f.prior_year_value) / abs(f.prior_year_value) end
                                                       as pct_change,
    case
        when f.year = f.first_year_in_scope   then 'no_prior_year_in_scope'
        when f.value is null                  then 'current_value_missing'
        when f.prior_year_value is null       then 'prior_value_missing'
        when f.prior_year_value = 0           then 'prior_value_zero'
        else 'ok'
    end                                                as yoy_status,
    f.last_available_prior_year,
    f.last_available_prior_value,
    case
        when f.value is not null
         and f.last_available_prior_value is not null
         and f.last_available_prior_value > 0
         and f.value > 0
        then power(f.value / f.last_available_prior_value,
                   1.0 / (f.year - f.last_available_prior_year)) - 1
    end                                                as annualised_pct_change_vs_last_available,
    -- lineage: the raw.api_response rows both sides of the comparison came from
    f.source_response_id,
    f.prior_source_response_id
from f
inner join {{ ref('dim_country') }} as c on f.country_iso3 = c.country_iso3
inner join {{ ref('dim_indicator') }} as i on f.indicator_code = i.indicator_code
