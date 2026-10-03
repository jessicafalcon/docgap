"""Read the dbt manifest: the mart schema `resolve` qualifies against, and the tags `lint` checks."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, ValidationError

from docgap.config import ManifestConfig
from docgap.models import Identifier, Sensitivity

__all__ = ["DBT_SCHEMA_VERSION", "Marts", "lint_manifest", "load_marts", "read_marts"]

# The manifest shape this reader was written against. Another version fails
# loading instead of being read under a guessed shape.
DBT_SCHEMA_VERSION = "https://schemas.getdbt.com/dbt/manifest/v12.json"
# ADR 0014: the same dbt project builds on DuckDB offline and on Snowflake.
_ADAPTERS = frozenset({"duckdb", "snowflake"})
_RELATIONS = frozenset({"model", "seed", "snapshot"})
_IDENTIFIER = TypeAdapter[str](Identifier)
_SENSITIVITIES = frozenset(sensitivity.value for sensitivity in Sensitivity)


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
    # A tag of any JSON type is read, so `lint` reports a wrong one instead of the
    # manifest failing to load.
    meta: dict[str, JsonValue] = {}


class _Column(_Read):
    name: str
    # dbt writes an empty string for a column with no description.
    description: str = ""
    data_type: str | None = None
    quote: bool | None = None
    # dbt 1.12 writes a column's meta both here and under its `config`.
    meta: dict[str, JsonValue] = {}


class _Node(_Read):
    resource_type: str
    unique_id: str
    description: str = ""
    database: str | None = None
    # `schema` would shadow a BaseModel attribute.
    schema_: str | None = Field(default=None, alias="schema")
    alias: str | None = None
    config: _NodeConfig = _NodeConfig()
    columns: dict[str, _Column] = {}


class _Manifest(_Read):
    metadata: _Metadata
    nodes: dict[str, _Node]


def _fqn(database: str, schema: str, table: str, column: str) -> str:
    return f"{database}.{schema}.{table}.{column}"


@dataclass(frozen=True)
class Marts:
    """The mart relations: table name to column name to dbt data type, uppercased.

    `documented` holds the FQNs of the columns with a description; every other
    column is "missing", the gaps `coverage` counts and `rank` ranks. The texts are
    what the test agent's `describe()` reads offline: tables by name, columns by FQN.
    """

    database: str
    schema: str
    tables: Mapping[str, Mapping[str, str]]
    column_descriptions: Mapping[str, str]
    table_descriptions: Mapping[str, str] = field(default_factory=dict[str, str])

    @property
    def documented(self) -> frozenset[str]:
        """The FQNs of the columns with a description."""
        return frozenset(self.column_descriptions)

    def fqns(self) -> list[str]:
        """Every mart column as `DATABASE.SCHEMA.TABLE.COLUMN`, sorted."""
        return sorted(
            _fqn(self.database, self.schema, table, column)
            for table, columns in self.tables.items()
            for column in columns
        )


def _identifier(name: str, where: str) -> str:
    # Snowflake stores an unquoted name uppercased; a name that isn't a plain
    # identifier once uppercased can't match the column FQNs `resolve` writes.
    try:
        return _IDENTIFIER.validate_python(name.upper())
    except ValidationError:
        raise ValueError(f"{where}: {name!r} is not an unquoted identifier") from None


def _mart_columns(node: _Node) -> tuple[dict[str, str], dict[str, str]]:
    if node.resource_type != "model" or not node.config.contract.enforced:
        # The manifest lists only the columns declared in YAML; without an enforced
        # contract a column the model builds could be missing, and never be counted.
        raise ValueError(
            f"{node.unique_id}: a mart relation must be a model with an enforced contract"
        )
    columns: dict[str, str] = {}
    described: dict[str, str] = {}
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
        # Whitespace alone tells the agent nothing, so it counts as no description.
        if column.description.strip():
            described[name] = column.description
    if not columns:
        raise ValueError(f"{node.unique_id}: no columns declared")
    return columns, described


def _parse(manifest: bytes) -> _Manifest:
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
    return parsed


def _nodes(parsed: _Manifest) -> list[_Node]:
    return sorted(parsed.nodes.values(), key=lambda node: node.unique_id)


def _in_marts(node: _Node, config: ManifestConfig) -> bool:
    return (
        node.resource_type in _RELATIONS
        and node.database is not None
        and node.schema_ is not None
        and (node.database.upper(), node.schema_.upper())
        == (config.mart_database, config.mart_schema)
    )


def _marts(parsed: _Manifest, config: ManifestConfig) -> Marts:
    tables: dict[str, dict[str, str]] = {}
    table_descriptions: dict[str, str] = {}
    column_descriptions: dict[str, str] = {}
    for node in _nodes(parsed):
        if not _in_marts(node, config):
            continue
        table = _identifier(node.alias or "", f"{node.unique_id} alias")
        if table in tables:
            raise ValueError(f"{node.unique_id}: a second relation named {table}")
        tables[table], described = _mart_columns(node)
        if node.description.strip():
            table_descriptions[table] = node.description
        column_descriptions.update(
            (_fqn(config.mart_database, config.mart_schema, table, column), text)
            for column, text in described.items()
        )
    if not tables:
        raise ValueError(
            f"dbt manifest: no relation in {config.mart_database}.{config.mart_schema}"
        )
    return Marts(
        config.mart_database,
        config.mart_schema,
        tables,
        column_descriptions,
        table_descriptions,
    )


def load_marts(manifest: bytes, config: ManifestConfig) -> Marts:
    """Read the models in the configured mart schema from a dbt `manifest.json`.

    Raises:
        ValueError: the manifest is another dbt schema version or adapter, the mart
            schema has no relation, or a mart relation is not a contracted model
            with typed, unquoted columns.
    """
    return _marts(_parse(manifest), config)


def lint_manifest(manifest: bytes, config: ManifestConfig) -> list[str]:
    """Return each gap in the metadata contract, one line per model or mart column.

    Every model needs a non-blank `meta.owner`, and every mart column a
    `meta.sensitivity` of `public`, `internal` or `restricted`. The marts must load
    first, so a manifest `analyze` can't read never passes.

    Raises:
        ValueError: as `load_marts`.
    """
    parsed = _parse(manifest)
    _marts(parsed, config)
    gaps: list[str] = []
    for node in _nodes(parsed):
        if node.resource_type != "model":
            continue
        owner = node.config.meta.get("owner")
        if not (isinstance(owner, str) and owner.strip()):
            gaps.append(f"{node.unique_id}: meta.owner is {owner!r}, expected a non-blank name")
        if not _in_marts(node, config):
            continue
        for column in node.columns.values():
            sensitivity = column.meta.get("sensitivity")
            if not (isinstance(sensitivity, str) and sensitivity in _SENSITIVITIES):
                gaps.append(
                    f"{node.unique_id}.{column.name}: meta.sensitivity is {sensitivity!r}, "
                    f"expected one of {sorted(_SENSITIVITIES)}"
                )
    return gaps


def read_marts(path: Path, config: ManifestConfig) -> tuple[Marts, str]:
    """Read a dbt manifest file into the marts, with the SHA-256 of the bytes parsed.

    The file is read once, so the hash a stage records covers exactly what it parsed.
    """
    manifest = path.read_bytes()
    return load_marts(manifest, config), hashlib.sha256(manifest).hexdigest()
