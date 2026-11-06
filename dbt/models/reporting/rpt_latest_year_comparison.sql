-- Q3: how do the selected countries compare for the latest available year of each indicator?
-- Grain: one row per (indicator, country).
--
-- "Latest available year" is ambiguous when countries publish at different speeds, so both
-- readings are given side by side:
--   * latest_year            - the most recent year in which ANY selected country has a value
--                              (countries without a value that year show NULL + a flag);
--   * latest_complete_year   - the most recent year in which ALL selected countries have a value
--                              (an apples-to-apples ranking).
--   * own_latest_year/value  - each country's own most recent value, for context.

with f as (
    select * from {{ ref('fct_indicator_value') }}
),

per_indicator as (
    select
        indicator_code,
        max(year) filter (where not is_missing) as latest_year
    from f
    group by indicator_code
),

complete_years as (
    select indicator_code, max(year) as latest_complete_year
    from (
        select indicator_code, year
        from f
        group by indicator_code, year
        having count(*) filter (where is_missing) = 0
    )
    group by indicator_code
),

own_latest as (
    select
        country_iso3,
        indicator_code,
        max(year) filter (where not is_missing)              as own_latest_year,
        arg_max(value, year) filter (where not is_missing)   as own_latest_value
    from f
    group by country_iso3, indicator_code
),

-- every (indicator, country) pair gets a row, even for an indicator with no data at all
-- (latest_year is then NULL): missing data stays visible instead of silently dropping out
pairs as (
    select distinct indicator_code, country_iso3 from f
),

base as (
    select
        pr.indicator_code,
        pr.country_iso3,
        p.latest_year,
        f_latest.value                                       as value_at_latest_year,
        f_latest.source_response_id                          as source_response_id_at_latest_year,
        cy.latest_complete_year,
        f_complete.value                                     as value_at_latest_complete_year,
        f_complete.source_response_id                        as source_response_id_at_latest_complete_year,
        o.own_latest_year,
        o.own_latest_value
    from pairs as pr
    left join per_indicator as p
        on p.indicator_code = pr.indicator_code
    left join f as f_latest
        on f_latest.indicator_code = pr.indicator_code
       and f_latest.country_iso3 = pr.country_iso3
       and f_latest.year = p.latest_year
    left join complete_years as cy
        on cy.indicator_code = pr.indicator_code
    left join f as f_complete
        on f_complete.indicator_code = pr.indicator_code
       and f_complete.country_iso3 = pr.country_iso3
       and f_complete.year = cy.latest_complete_year
    left join own_latest as o
        on o.indicator_code = pr.indicator_code
       and o.country_iso3 = pr.country_iso3
)

select
    b.indicator_code,
    i.short_name                                                     as indicator_short_name,
    i.unit,
    b.country_iso3,
    c.country_name,
    c.income_group_name,
    b.latest_year,
    b.value_at_latest_year,
    b.value_at_latest_year is null                                   as missing_at_latest_year,
    case when b.value_at_latest_year is not null then
        rank() over (partition by b.indicator_code
                     order by b.value_at_latest_year desc nulls last) end
                                                                     as rank_at_latest_year,
    b.value_at_latest_year
        / nullif(median(b.value_at_latest_year) over (partition by b.indicator_code), 0)
                                                                     as ratio_to_peer_median,
    b.latest_complete_year,
    b.value_at_latest_complete_year,
    case when b.value_at_latest_complete_year is not null then
        rank() over (partition by b.indicator_code
                     order by b.value_at_latest_complete_year desc nulls last) end
                                                                     as rank_at_latest_complete_year,
    b.own_latest_year,
    b.own_latest_value,
    b.source_response_id_at_latest_year,
    b.source_response_id_at_latest_complete_year
from base as b
inner join {{ ref('dim_country') }} as c on b.country_iso3 = c.country_iso3
inner join {{ ref('dim_indicator') }} as i on b.indicator_code = i.indicator_code
