{{ config(severity='warn') }}

-- Time-series plausibility (warning): national population does not move more than 5% in a
-- year outside of extraordinary events. A jump usually signals a unit/definition change or a
-- bad revision upstream and deserves a human look before it reaches a report.

select country_iso3, year, prior_year_value, value, pct_change
from {{ ref('rpt_yoy_change') }}
where indicator_code = 'SP.POP.TOTL'
  and yoy_status = 'ok'
  and abs(pct_change) > 0.05
