"""Read the dbt manifest into the mart schema that `resolve` qualifies queries against."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from docgap.config import ManifestConfig
from docgap.models import Identifier

__all__ = ["DBT_SCHEMA_VERSION", "Marts", "load_marts"]

# The manifest shape this reader was written against. Another version fails
# loading instead of being read under a guessed shape.
DBT_SCHEMA_VERSION = "https://schemas.getdbt.com/dbt/manifest/v12.json"
# ADR 0014: the same dbt project builds on DuckDB offline and on Snowflake.
_ADAPTERS = frozenset({"duckdb", "snowflake"})
_RELATIONS = frozenset({"model", "seed", "snapshot"})
_IDENTIFIER = TypeAdapter[str](Identifier)


class _Read(BaseModel):
    # The manifest is dbt's file, not a contract: only the fields read are declared.
    model_config = ConfigDict(frozen=True, extra="ignore")


class _Metadata(_Read):
    dbt_schema_version: str
    adapter_type: str


class _ContractConfig(_Read):
    enforced: bool = False


class _NodeConfig(_Read):
    contract: _ContractConfig = _ContractConfig()


class _Column(_Read):
    name: str
    data_type: str | None = None
    quote: bool | None = None


class _Node(_Read):
    resource_type: str
    unique_id: str
    database: str | None = None
    # `schema` would shadow a BaseModel attribute.
    schema_: str | None = Field(default=None, alias="schema")
    alias: str | None = None
    config: _NodeConfig = _NodeConfig()
    columns: dict[str, _Column] = {}


class _Manifest(_Read):
    metadata: _Metadata
    nodes: dict[str, _Node]


@dataclass(frozen=True)
class Marts:
    """The mart relations: table name to column name to dbt data type, uppercased."""

    database: str
    schema: str
    tables: Mapping[str, Mapping[str, str]]


def _identifier(name: str, where: str) -> str:
    # Snowflake stores an unquoted name uppercased; a name that isn't a plain
    # identifier once uppercased can't match the column FQNs `resolve` writes.
    try:
        return _IDENTIFIER.validate_python(name.upper())
    except ValidationError:
        raise ValueError(f"{where}: {name!r} is not an unquoted identifier") from None


def _mart_columns(node: _Node) -> dict[str, str]:
    if node.resource_type != "model" or not node.config.contract.enforced:
        # The manifest lists only the columns declared in YAML; without an enforced
        # contract a column the model builds could be missing, and never be counted.
        raise ValueError(
            f"{node.unique_id}: a mart relation must be a model with an enforced contract"
        )
    columns: dict[str, str] = {}
    for column in node.columns.values():
        where = f"{node.unique_id}.{column.name}"
        if column.quote and column.name != column.name.upper():
            raise ValueError(f"{where}: a quoted mixed-case column is not supported")
        if not column.data_type:
            raise ValueError(f"{where}: no data_type")
        name = _identifier(column.name, where)
        if name in columns:
            raise ValueError(f"{where}: declared twice")
        columns[name] = column.data_type
    if not columns:
        raise ValueError(f"{node.unique_id}: no columns declared")
    return columns


def load_marts(manifest: bytes, config: ManifestConfig) -> Marts:
    """Read the models in the configured mart schema from a dbt `manifest.json`.

    Raises:
        ValueError: the manifest is another dbt schema version or adapter, the mart
            schema has no relation, or a mart relation is not a contracted model
            with typed, unquoted columns.
    """
    try:
        parsed = _Manifest.model_validate_json(manifest)
    except ValidationError as error:
        raise ValueError(f"dbt manifest: {error}") from None
    if parsed.metadata.dbt_schema_version != DBT_SCHEMA_VERSION:
        raise ValueError(
            f"dbt manifest: schema version {parsed.metadata.dbt_schema_version}, expected {DBT_SCHEMA_VERSION}"
        )
    if parsed.metadata.adapter_type not in _ADAPTERS:
        raise ValueError(
            f"dbt manifest: adapter {parsed.metadata.adapter_type!r}, expected one of {sorted(_ADAPTERS)}"
        )
    tables: dict[str, dict[str, str]] = {}
    for node in sorted(parsed.nodes.values(), key=lambda node: node.unique_id):
        if node.resource_type not in _RELATIONS or node.database is None or node.schema_ is None:
            continue
        if (node.database.upper(), node.schema_.upper()) != (
            config.mart_database,
            config.mart_schema,
        ):
            continue
        table = _identifier(node.alias or "", f"{node.unique_id} alias")
        if table in tables:
            raise ValueError(f"{node.unique_id}: a second relation named {table}")
        tables[table] = _mart_columns(node)
    if not tables:
        raise ValueError(
            f"dbt manifest: no relation in {config.mart_database}.{config.mart_schema}"
        )
    return Marts(config.mart_database, config.mart_schema, tables)
