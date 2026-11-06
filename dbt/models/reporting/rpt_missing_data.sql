-- Q1 (row level): which country-indicator-year combinations are missing?
-- Grain: one row per missing (country, indicator, year).
-- gap_type separates "not published yet" (after the last year the country has data for -
-- typically publication lag) from a genuine hole in the series.

with f as (
    select
        *,
        max(case when not is_missing then year end)
            over (partition by country_iso3, indicator_code) as last_available_year,
        min(case when not is_missing then year end)
            over (partition by country_iso3, indicator_code) as first_available_year
    from {{ ref('fct_indicator_value') }}
),

-- the observation request(s) that were checked for each indicator in the current run: proof
-- of absence for rows the API did not return at all
checked_requests as (
    select
        entity                                                 as indicator_code,
        any_value(fetched_in_run_id)                           as checked_in_run_id,
        string_agg(request_url, ' | ' order by page)           as checked_request_urls
    from {{ ref('stg_wb__current_responses') }}
    where endpoint = 'observation'
    group by entity
)

select
    f.country_iso3,
    c.country_name,
    c.income_group_name,
    f.indicator_code,
    i.short_name           as indicator_short_name,
    f.year,
    f.missing_reason,
    case
        when f.last_available_year is null       then 'no_data_in_scope'
        when f.year > f.last_available_year      then 'not_yet_published'
        when f.year < f.first_available_year     then 'before_series_start'
        else 'gap_in_series'
    end                    as gap_type,
    f.last_available_year,
    -- lineage: for null_in_source, the response that returned the null; for
    -- not_returned_by_source there is no row to point at (the request is in raw.api_fetch_log)
    f.source_response_id,
    q.checked_in_run_id,
    coalesce(f.source_request_url, q.checked_request_urls)    as checked_request_urls
from f
left join checked_requests as q on f.indicator_code = q.indicator_code
inner join {{ ref('dim_country') }} as c on f.country_iso3 = c.country_iso3
inner join {{ ref('dim_indicator') }} as i on f.indicator_code = i.indicator_code
where f.is_missing
