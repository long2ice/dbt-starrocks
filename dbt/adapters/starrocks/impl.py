from concurrent.futures import Future
from enum import Enum
from typing import Callable, Dict, List, Optional, Set, Tuple

import agate
import dbt.exceptions
from dbt.adapters.base.impl import _expect_row_value, catch_as_completed
from dbt.adapters.base.relation import InformationSchema
from dbt.adapters.protocol import AdapterConfig
from dbt.adapters.sql import SQLAdapter
from dbt.adapters.sql.impl import LIST_RELATIONS_MACRO_NAME, LIST_SCHEMAS_MACRO_NAME
from dbt.clients.agate_helper import table_from_rows
from dbt.contracts.graph.manifest import Manifest
from dbt.contracts.relation import RelationType
from dbt.utils import executor

from dbt.adapters.starrocks.column import StarRocksColumn
from dbt.adapters.starrocks.connections import StarRocksAdapterConnectionManager
from dbt.adapters.starrocks.relation import StarRocksRelation


class Engine(str, Enum):
    olap = "olap"
    mysql = "mysql"
    elasticsearch = "elasticsearch"
    hive = "hive"
    iceberg = "iceberg"


class StarRocksConfig(AdapterConfig):
    engine: Engine = Engine.olap
    duplicate_key: Tuple[str]
    partition_by: Tuple[str]
    partition_by_init: List[str]
    distributed_by: Tuple[str]
    buckets: int
    properties: Dict[str, str]


class StarRocksAdapter(SQLAdapter):
    ConnectionManager = StarRocksAdapterConnectionManager
    Relation = StarRocksRelation
    AdapterSpecificConfigs = StarRocksConfig
    Column = StarRocksColumn

    @classmethod
    def date_function(cls) -> str:
        return "current_date()"

    @classmethod
    def convert_datetime_type(cls, agate_table: agate.Table, col_idx: int) -> str:
        return "datetime"

    @classmethod
    def convert_text_type(cls, agate_table: agate.Table, col_idx: int) -> str:
        return "string"

    def quote(self, identifier):
        return "`{}`".format(identifier)

    def check_schema_exists(self, database, schema):
        results = self.execute_macro(
            LIST_SCHEMAS_MACRO_NAME, kwargs={"database": database}
        )

        exists = True if schema in [row[0] for row in results] else False
        return exists

    def get_relation(self, database: Optional[str], schema: str, identifier: str):
        if not self.Relation.include_policy.database:
            database = None

        return super().get_relation(database, schema, identifier)

    def list_relations_without_caching(
        self, schema_relation: StarRocksRelation
    ) -> List[StarRocksRelation]:
        kwargs = {"schema_relation": schema_relation}
        results = self.execute_macro(LIST_RELATIONS_MACRO_NAME, kwargs=kwargs)

        relations = []
        for row in results:
            if len(row) != 4:
                raise dbt.exceptions.RuntimeException(
                    f"Invalid value from 'show table extended ...', "
                    f"got {len(row)} values, expected 4"
                )
            _database, name, schema, type_info = row
            rel_type = RelationType.View if "view" in type_info else RelationType.Table
            relation = self.Relation.create(
                database=None,
                schema=schema,
                identifier=name,
                type=rel_type,
            )
            relations.append(relation)

        return relations

    def get_catalog(self, manifest):
        schema_map = self._get_catalog_schemas(manifest)
        if len(schema_map) > 1:
            dbt.exceptions.raise_compiler_error(
                f"Expected only one database in get_catalog, found "
                f"{list(schema_map)}"
            )

        with executor(self.config) as tpe:
            futures: List[Future[agate.Table]] = []
            for info, schemas in schema_map.items():
                for schema in schemas:
                    futures.append(
                        tpe.submit_connected(
                            self,
                            schema,
                            self._get_one_catalog,
                            info,
                            [schema],
                            manifest,
                        )
                    )
            catalogs, exceptions = catch_as_completed(futures)
        return catalogs, exceptions

    @classmethod
    def _catalog_filter_table(
        cls, table: agate.Table, manifest: Manifest
    ) -> agate.Table:
        table = table_from_rows(
            table.rows,
            table.column_names,
            text_only_columns=["table_schema", "table_name"],
        )
        return table.where(_catalog_filter_schemas(manifest))

    def _get_one_catalog(
        self,
        information_schema: InformationSchema,
        schemas: Set[str],
        manifest: Manifest,
    ) -> agate.Table:
        if len(schemas) != 1:
            dbt.exceptions.raise_compiler_error(
                f"Expected only one schema in StarRocks _get_one_catalog, found "
                f"{schemas}"
            )

        return super()._get_one_catalog(information_schema, schemas, manifest)


def _catalog_filter_schemas(manifest: Manifest) -> Callable[[agate.Row], bool]:
    schemas = frozenset((None, s.lower()) for d, s in manifest.get_used_schemas())

    def test(row: agate.Row) -> bool:
        table_database = _expect_row_value("table_database", row)
        table_schema = _expect_row_value("table_schema", row)
        if table_schema is None:
            return False
        return (table_database, table_schema.lower()) in schemas

    return test
