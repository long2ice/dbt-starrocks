{% macro starrocks__list_relations_without_caching(schema_relation) -%}
  {% call statement('list_relations_without_caching', fetch_result=True) %}
    select
      null as "database",
      table_name as name,
      table_schema as "schema",
      case when table_type = 'BASE TABLE' then 'table'
           when table_type = 'VIEW' then 'view'
           else table_type end as table_type
    from information_schema.tables
    where table_schema = '{{ schema_relation.schema }}'
  {% endcall %}
  {{ return(load_result('list_relations_without_caching').table) }}
{%- endmacro %}

{% macro starrocks__get_catalog(information_schema, schemas) -%}
    {%- call statement('catalog', fetch_result=True) -%}
    with tables as (
        select
            null as "table_database",
            table_schema,
            table_name,
            case when table_type = 'BASE TABLE' then 'table'
                 when table_type = 'VIEW' then 'view'
                 else table_type
            end as table_type,
            null as table_owner
        from {{ information_schema }}.tables
    ),
    columns as (
        select
            null as "table_database",
            table_schema as "table_schema",
            table_name as "table_name",
            null as "table_comment",
            column_name as "column_name",
            ordinal_position as "column_index",
            data_type as "column_type",
            null as "column_comment"
        from {{ information_schema }}.columns
    )
    select
        columns.table_database,
        columns.table_schema,
        columns.table_name,
        tables.table_type,
        columns.table_comment,
        tables.table_owner,
        columns.column_name,
        columns.column_index,
        columns.column_type,
        columns.column_comment
    from tables
    join columns using (table_schema, table_name)
    where tables.table_schema not in ('information_schema', '__statistics__')
    and (
    {%- for schema in schemas -%}
      upper(tables.table_schema) = upper('{{ schema }}'){%- if not loop.last %} or {% endif -%}
    {%- endfor -%}
    )
    order by column_index
    {%- endcall -%}

    {{ return(load_result('catalog').table) }}

{%- endmacro %}

{% macro starrocks__check_schema_exists(database, schema) -%}
{%- endmacro %}

{% macro starrocks__list_schemas(database) -%}
    {% call statement('list_schemas', fetch_result=True, auto_begin=False) -%}
    select distinct schema_name from information_schema.schemata
    {%- endcall %}
    {{ return(load_result('list_schemas').table) }}
{%- endmacro %}
