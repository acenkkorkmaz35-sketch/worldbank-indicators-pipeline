-- Plausibility: each value lies within the valid_range configured for its indicator
-- (config/pipeline.yml). Catches unit errors (e.g. GDP in millions) and sign flips.

select f.country_iso3, f.indicator_code, f.year, f.value, i.valid_min, i.valid_max
from {{ ref('fct_indicator_value') }} as f
inner join {{ ref('dim_indicator') }} as i on f.indicator_code = i.indicator_code
where f.value is not null
  and (
        (i.valid_min is not null and f.value < i.valid_min)
     or (i.valid_max is not null and f.value > i.valid_max)
  )
