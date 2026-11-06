-- Grain: one row per indicator in scope (business key: indicator_code).
-- Combines analyst-facing config (short name, unit, aggregation rule, valid range) with the
-- API's own metadata (official name, definition, source organisation).

select
    s.indicator_code,
    s.short_name,
    coalesce(i.indicator_name, s.indicator_code)  as indicator_name,
    s.unit,
    s.aggregation_method,
    s.numerator_code,
    s.denominator_code,
    s.weight_code,
    s.valid_min,
    s.valid_max,
    i.source_database,
    i.source_organization,
    i.definition,
    s.sort_order,
    i.indicator_code is not null                  as has_source_metadata,
    i.source_response_id
from {{ source('meta', 'scope_indicator') }} as s
left join {{ ref('stg_wb__indicators') }} as i
    on s.indicator_code = i.indicator_code
