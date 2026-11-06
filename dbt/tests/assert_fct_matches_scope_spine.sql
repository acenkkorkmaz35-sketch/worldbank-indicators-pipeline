-- Grain completeness: the fact table holds exactly the expected
-- scope_country x scope_indicator x scope_year combinations - no more, no fewer.

with expected as (
    select c.country_iso3, i.indicator_code, y.year
    from {{ source('meta', 'scope_country') }} as c
    cross join {{ source('meta', 'scope_indicator') }} as i
    cross join {{ source('meta', 'scope_year') }} as y
),

actual as (
    select country_iso3, indicator_code, year from {{ ref('fct_indicator_value') }}
)

select 'missing_from_fact' as problem, * from (select * from expected except select * from actual)
union all
select 'unexpected_in_fact' as problem, * from (select * from actual except select * from expected)
