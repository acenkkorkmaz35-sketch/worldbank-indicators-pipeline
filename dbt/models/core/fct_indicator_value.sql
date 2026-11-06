{#-
  Grain: exactly one row per (country_iso3, indicator_code, year) in the configured scope.
  The row exists whether or not the source has a value: missing data is a first-class outcome,
  described by is_missing + missing_reason, never by a zero.

  Load pattern (mirrors a Snowflake MERGE):
    * the spine (scope_country x scope_indicator x scope_year) is joined to current observations;
    * on incremental runs only rows whose content hash changed are sent to the MERGE, so
      re-running with unchanged source data touches zero rows (idempotent, no duplicates);
    * first_loaded_at is preserved on update, last_changed_at moves only on real changes;
    * a post-hook removes keys that dropped out of scope, so the table always equals the spine.
-#}
{{
  config(
    materialized='incremental',
    incremental_strategy='merge',
    unique_key=['country_iso3', 'indicator_code', 'year'],
    merge_update_condition='DBT_INTERNAL_DEST.row_hash is distinct from DBT_INTERNAL_SOURCE.row_hash',
    merge_exclude_columns=['first_loaded_at'],
    on_schema_change='fail',
    post_hook=[
      "delete from {{ this }} as t
       where not exists (select 1 from {{ source('meta', 'scope_country') }} s where s.country_iso3 = t.country_iso3)
          or not exists (select 1 from {{ source('meta', 'scope_indicator') }} s where s.indicator_code = t.indicator_code)
          or not exists (select 1 from {{ source('meta', 'scope_year') }} s where s.year = t.year)"
    ]
  )
}}

with spine as (
    select c.country_iso3, i.indicator_code, y.year
    from {{ source('meta', 'scope_country') }} as c
    cross join {{ source('meta', 'scope_indicator') }} as i
    cross join {{ source('meta', 'scope_year') }} as y
),

joined as (
    select
        spine.country_iso3,
        spine.indicator_code,
        spine.year,
        o.value,
        o.value is null                                   as is_missing,
        case
            when o.country_iso3 is null then 'not_returned_by_source'
            when o.value is null        then 'null_in_source'
        end                                               as missing_reason,
        o.obs_status,
        o.decimal_places,
        o.source_last_updated,
        o.source_response_id,
        o.source_request_url,
        o.source_page,
        o.source_run_id
    from spine
    left join {{ ref('stg_wb__observations') }} as o
        on spine.country_iso3 = o.country_iso3
       and spine.indicator_code = o.indicator_code
       and spine.year = o.year
),

hashed as (
    select
        md5(concat_ws('|', country_iso3, indicator_code, year))   as indicator_value_id,
        *,
        -- Business content only: a new API release that re-publishes the same value is not a
        -- change (the original response still holds the value, so lineage stays truthful).
        md5(concat_ws('|',
            coalesce(cast(value as varchar), '<null>'),
            coalesce(missing_reason, '<present>'),
            coalesce(obs_status, '<none>'),
            coalesce(cast(decimal_places as varchar), '<none>')
        ))                                                         as row_hash,
        current_timestamp                                          as first_loaded_at,
        current_timestamp                                          as last_changed_at
    from joined
)

select h.*
from hashed as h
{% if is_incremental() %}
left join {{ this }} as t
    on h.country_iso3 = t.country_iso3
   and h.indicator_code = t.indicator_code
   and h.year = t.year
where t.row_hash is distinct from h.row_hash
{% endif %}
