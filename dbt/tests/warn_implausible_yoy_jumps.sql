{{ config(severity='warn') }}

-- Time-series plausibility (warning): flags year-over-year moves larger than the indicator's
-- configured max_abs_yoy_change (config/pipeline.yml), e.g. population > 5% in a year.
-- A jump usually signals a definition/unit change or a bad upstream revision and deserves a
-- human look before it reaches a report. Indicators without a threshold are not checked.

select
    y.country_iso3,
    y.indicator_code,
    y.year,
    y.prior_year_value,
    y.value,
    y.pct_change,
    i.max_abs_yoy_change
from {{ ref('rpt_yoy_change') }} as y
inner join {{ ref('dim_indicator') }} as i on y.indicator_code = i.indicator_code
where i.max_abs_yoy_change is not null
  and y.yoy_status = 'ok'
  and abs(y.pct_change) > i.max_abs_yoy_change
