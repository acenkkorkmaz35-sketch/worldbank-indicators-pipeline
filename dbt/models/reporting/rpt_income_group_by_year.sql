-- Q4: how do the indicators compare across income groups, by year?
-- Grain: one row per (income group, indicator, year), plus an 'All selected countries' row per
-- (indicator, year) for reference.
--
-- Aggregation is driven by config (dim_indicator.aggregation_method), never by averaging
-- country-level ratios:
--   sum           population, GDP                    -> sum of values
--   ratio         GDP per capita                     -> sum(GDP) / sum(population)
--   weighted_avg  life expectancy                    -> population-weighted mean
-- Only countries where every input is present contribute. countries_contributing vs
-- countries_in_group makes partial coverage explicit (is_complete); a 'sum' over an incomplete
-- group understates the total and should not be compared across years without that flag.
-- Income group = the World Bank's *current* classification, applied to all years.

with f as (
    select f.*, c.income_group_code, c.income_group_name
    from {{ ref('fct_indicator_value') }} as f
    inner join {{ ref('dim_country') }} as c on f.country_iso3 = c.country_iso3
),

inputs as (
    select
        f.income_group_code,
        f.income_group_name,
        f.indicator_code,
        i.short_name,
        i.unit,
        i.aggregation_method,
        f.year,
        f.country_iso3,
        f.value,
        num.value as numerator_value,
        den.value as denominator_value,
        wt.value  as weight_value,
        case i.aggregation_method
            when 'sum'          then f.value is not null
            when 'ratio'        then num.value is not null and den.value is not null and den.value <> 0
            when 'weighted_avg' then f.value is not null and wt.value is not null and wt.value > 0
        end       as contributes
    from f
    inner join {{ ref('dim_indicator') }} as i on f.indicator_code = i.indicator_code
    left join f as num
        on num.country_iso3 = f.country_iso3 and num.year = f.year and num.indicator_code = i.numerator_code
    left join f as den
        on den.country_iso3 = f.country_iso3 and den.year = f.year and den.indicator_code = i.denominator_code
    left join f as wt
        on wt.country_iso3 = f.country_iso3 and wt.year = f.year and wt.indicator_code = i.weight_code
),

aggregated as (
    select
        coalesce(income_group_code, 'ALL')                         as income_group_code,
        coalesce(income_group_name, 'All selected countries')      as income_group_name,
        indicator_code,
        short_name                                                 as indicator_short_name,
        unit,
        aggregation_method,
        year,
        count(distinct country_iso3)                               as countries_in_group,
        count(distinct country_iso3) filter (where contributes)    as countries_contributing,
        string_agg(country_iso3, ', ' order by country_iso3) filter (where contributes)
                                                                   as contributing_countries,
        case any_value(aggregation_method)
            when 'sum' then sum(value) filter (where contributes)
            when 'ratio' then
                sum(numerator_value) filter (where contributes)
                / nullif(sum(denominator_value) filter (where contributes), 0)
            when 'weighted_avg' then
                sum(value * weight_value) filter (where contributes)
                / nullif(sum(weight_value) filter (where contributes), 0)
        end                                                        as group_value,
        avg(value)                                                 as simple_avg_of_countries,
        min(value)                                                 as min_country_value,
        max(value)                                                 as max_country_value,
        grouping(income_group_code)                                as is_all_countries_row
    from inputs
    group by grouping sets (
        (income_group_code, income_group_name, indicator_code, short_name, unit, aggregation_method, year),
        (indicator_code, short_name, unit, aggregation_method, year)
    )
)

select
    * exclude (is_all_countries_row),
    countries_contributing = countries_in_group as is_complete,
    is_all_countries_row = 1                    as is_all_countries_row
from aggregated
