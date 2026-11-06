-- Extra: audit of source corrections and late-arriving data, from the SCD2 snapshot.
-- Grain: one row per superseded version of a fact (i.e. every change the source made after
-- we first loaded a value). Empty until the World Bank revises something between runs.

with versions as (
    select
        country_iso3,
        indicator_code,
        year,
        value,
        missing_reason,
        dbt_valid_from,
        dbt_valid_to,
        lead(value) over w           as new_value,
        lead(missing_reason) over w  as new_missing_reason,
        lead(dbt_valid_from) over w  as next_version_from
    from {{ ref('snap_indicator_value') }}
    window w as (partition by indicator_value_id order by dbt_valid_from)
)

select
    country_iso3,
    indicator_code,
    year,
    value                as old_value,
    new_value,
    missing_reason       as old_missing_reason,
    new_missing_reason,
    case
        when next_version_from is null               then 'removed_from_scope'
        when value is null and new_value is not null then 'late_arriving_value'
        when value is not null and new_value is null then 'value_withdrawn'
        else 'value_revised'
    end                  as change_type,
    case when value <> 0 then (new_value - value) / abs(value) end as revision_pct,
    dbt_valid_to         as changed_at
from versions
where dbt_valid_to is not null
