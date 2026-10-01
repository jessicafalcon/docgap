{#- STAGING and MARTS by their own names, as on Snowflake. dbt's default prefixes the
    target schema (MAIN_MARTS), and the column FQNs docgap resolves would differ. -#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {{ (custom_schema_name or target.schema) | trim }}
{%- endmacro %}
