{#- Generic test: no two rows share the same values across the given columns (grain check).
    Inlined instead of pulling in dbt_utils so the project builds fully offline. -#}
{% test unique_combination_of_columns(model, combination_of_columns) %}
    select {{ combination_of_columns | join(', ') }}, count(*) as n_rows
    from {{ model }}
    group by {{ combination_of_columns | join(', ') }}
    having count(*) > 1
{% endtest %}
