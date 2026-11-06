-- Missing-data semantics: is_missing <=> value is null <=> missing_reason is not null,
-- and a missing value never carries lineage that claims otherwise.

select *
from {{ ref('fct_indicator_value') }}
where is_missing <> (value is null)
   or is_missing <> (missing_reason is not null)
   or (missing_reason = 'null_in_source' and source_response_id is null)
   or (missing_reason = 'not_returned_by_source' and source_response_id is not null)
   or (not is_missing and source_response_id is null)
