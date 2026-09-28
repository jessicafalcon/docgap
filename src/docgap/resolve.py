"""Resolve each snapshot query to the mart columns it reads, with the clause each appears in."""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Iterable, Iterator
from enum import StrEnum
from pathlib import Path

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.pushdown_projections import pushdown_projections
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, traverse_scope
from sqlglot.schema import MappingSchema

from docgap.artifacts import read_rows, rows_sha256, write_rows
from docgap.config import ManifestConfig
from docgap.manifest import Marts, load_marts
from docgap.models import Clause, ColumnRef, QueryRecord, StageRecord

__all__ = ["COLUMN_REFS_FILE", "Reference", "resolve", "resolve_query", "run_resolve"]

COLUMN_REFS_FILE = "column_refs.parquet"
_DIALECT = "snowflake"
_INFORMATION_SCHEMA = "INFORMATION_SCHEMA"
# The arg of a SELECT that holds the column, as sqlglot names it. Anything else
# (QUALIFY, a lateral) is `OTHER`, still counted.
_CLAUSES = {
    "expressions": Clause.SELECT,
    "where": Clause.WHERE,
    "joins": Clause.JOIN,
    "group": Clause.GROUP_BY,
    "having": Clause.HAVING,
    "order": Clause.ORDER_BY,
}


class Reference(StrEnum):
    """What a column reference turned out to be. Each is counted; only `RESOLVED` is kept."""

    RESOLVED = "resolved"
    # A fully qualified relation outside the marts: a staging table, another database.
    UNMANAGED = "unmanaged"
    # The agent's `describe()` on Snowflake, not a read of the data.
    INFORMATION_SCHEMA = "information_schema"
    # An unknown column, an ambiguous name, a quoted lowercase name, or a table name
    # the session context can't qualify.
    UNRESOLVED = "unresolved"


def _clause(node: exp.Expr, scope: Scope) -> Clause:
    while node.parent is not None and node.parent is not scope.expression:
        node = node.parent
    return _CLAUSES.get(node.arg_key or "", Clause.OTHER)


def _table_kind(table: exp.Table, marts: Marts) -> Reference:
    if not (table.catalog and table.db):
        return Reference.UNRESOLVED
    if table.db == _INFORMATION_SCHEMA:
        return Reference.INFORMATION_SCHEMA
    if (table.catalog, table.db) == (marts.database, marts.schema) and table.name in marts.tables:
        return Reference.RESOLVED
    return Reference.UNMANAGED


def _unattributed(scope: Scope, marts: Marts) -> Reference:
    # A name qualify couldn't tie to a source. When every relation in scope is outside
    # the marts it reads one of them; otherwise it is unknown or ambiguous.
    kinds = {
        _table_kind(source, marts)
        for source in scope.sources.values()
        if isinstance(source, exp.Table)
    }
    if len(kinds) == 1 and kinds <= {Reference.UNMANAGED, Reference.INFORMATION_SCHEMA}:
        return kinds.pop()
    return Reference.UNRESOLVED


def _references(scope: Scope, marts: Marts) -> Iterator[tuple[Reference, str, Clause]]:
    # A set operation's own columns name its branches' outputs, counted in each branch.
    if isinstance(scope.expression, exp.SetOperation):
        return
    external = (
        {id(column) for column in scope.external_columns} if not scope.is_root else set[int]()
    )
    for column in scope.columns:
        # A correlated reference is also listed, and counted, in the scope that owns it.
        if id(column) in external:
            continue
        source = scope.sources.get(column.table)
        clause = _clause(column, scope)
        if isinstance(source, Scope):
            # A CTE, derived table or table function: the columns it reads are
            # counted in its own scope. A name it doesn't output reads nothing.
            outputs = (
                source.expression.named_selects
                if isinstance(source.expression, exp.Query)
                else ["*"]
            )
            if column.name not in outputs and "*" not in outputs:
                yield Reference.UNRESOLVED, column.sql(dialect=_DIALECT), clause
            continue
        if not isinstance(source, exp.Table):
            yield _unattributed(scope, marts), column.sql(dialect=_DIALECT), clause
            continue
        kind = _table_kind(source, marts)
        if kind is Reference.RESOLVED:
            if column.name in marts.tables[source.name]:
                yield kind, f"{source.catalog}.{source.db}.{source.name}.{column.name}", clause
            else:
                yield Reference.UNRESOLVED, column.sql(dialect=_DIALECT), clause
        else:
            yield kind, column.sql(dialect=_DIALECT), clause
    # A `*` or `T.*` left after qualify reads a relation whose columns aren't known.
    # Over a CTE or subquery only, it is counted in that scope.
    if not isinstance(scope.expression, exp.Select):
        return
    tables = any(isinstance(source, exp.Table) for source in scope.sources.values())
    for select in scope.expression.selects:
        if not select.is_star:
            continue
        source = scope.sources.get(select.table) if isinstance(select, exp.Column) else None
        if isinstance(source, exp.Table):
            yield _table_kind(source, marts), select.sql(dialect=_DIALECT), Clause.SELECT
        elif source is None and tables:
            yield _unattributed(scope, marts), select.sql(dialect=_DIALECT), Clause.SELECT


def _top_level_star(tree: exp.Expr) -> bool:
    if isinstance(tree, exp.SetOperation):
        return _top_level_star(tree.left) or _top_level_star(tree.right)
    return isinstance(tree, exp.Select) and any(select.is_star for select in tree.selects)


def resolve_query(
    sql: str, *, database: str | None, schema: str | None, marts: Marts
) -> tuple[set[tuple[Reference, str, Clause]], bool]:
    """Qualify one normalized query against the marts and list its distinct column references.

    Unqualified table names take the session's `database` and `schema`, as
    Snowflake resolves them. A resolved reference is named by its column FQN.
    Also returns whether the outer query selects `*`, which reads every column.

    >>> marts = Marts("ANALYTICS", "MARTS", {"T": {"A": "NUMBER", "B": "NUMBER"}})
    >>> refs, star = resolve_query(
    ...     "WITH C AS (SELECT * FROM T) SELECT A FROM C WHERE Z = ?",
    ...     database="ANALYTICS", schema="MARTS", marts=marts)
    >>> sorted(refs), star
    ([(<Reference.RESOLVED: 'resolved'>, 'ANALYTICS.MARTS.T.A', <Clause.SELECT: 'select'>), \
(<Reference.UNRESOLVED: 'unresolved'>, 'Z', <Clause.WHERE: 'where'>)], False)

    Raises:
        sqlglot.errors.SqlglotError: sqlglot can't parse or qualify the query.
    """
    tree = sqlglot.parse_one(sql, dialect=_DIALECT)
    star = _top_level_star(tree)
    mapping = MappingSchema(marts.sqlglot_schema(), dialect=_DIALECT, normalize=False)
    # Without column validation: validation fails the whole query on one unknown
    # name, and every other column in it would go uncounted.
    tree = qualify(
        tree,
        schema=mapping,
        dialect=_DIALECT,
        catalog=database,
        db=schema,
        validate_qualify_columns=False,
        allow_partial_qualification=True,
        quote_identifiers=False,
    )
    # A `*` inside a CTE or subquery then counts only the columns the outer query reads.
    tree = pushdown_projections(tree, schema=mapping)
    refs = {ref for scope in traverse_scope(tree) for ref in _references(scope, marts)}
    return refs, star


def resolve(records: Iterable[QueryRecord], marts: Marts) -> tuple[list[ColumnRef], dict[str, int]]:
    """Resolve every query in the snapshot, and count each reference by what it turned out to be.

    Rows are sorted by query ID, column FQN and clause. A query sqlglot can't
    qualify is counted and contributes no rows.
    """
    rows: list[ColumnRef] = []
    references: Counter[Reference] = Counter()
    queries = star = failed = 0
    for record in records:
        queries += 1
        try:
            refs, top_level_star = resolve_query(
                record.normalized_sql,
                database=record.database_name,
                schema=record.schema_name,
                marts=marts,
            )
        except SqlglotError:
            failed += 1
            continue
        star += top_level_star
        for kind, name, clause in refs:
            references[kind] += 1
            if kind is Reference.RESOLVED:
                rows.append(ColumnRef(query_id=record.query_id, fqn=name, clause=clause))
    rows.sort(key=lambda row: (row.query_id, row.fqn, row.clause))
    counts = {
        "queries.read": queries,
        "queries.qualify_failed": failed,
        "queries.top_level_star": star,
    } | {f"columns.{kind.value}": references[kind] for kind in Reference}
    return rows, counts


def run_resolve(
    snapshot: Path,
    manifest: Path,
    *,
    config: ManifestConfig,
    setup_sha256: str,
    out_dir: Path,
) -> StageRecord:
    """Resolve `query_snapshot.parquet` against the dbt manifest into `column_refs.parquet`."""
    records = read_rows(snapshot, QueryRecord)
    # Read once, so the input hash covers exactly the bytes parsed.
    manifest_bytes = manifest.read_bytes()
    rows, counts = resolve(records, load_marts(manifest_bytes, config))
    out_dir.mkdir(parents=True, exist_ok=True)
    return StageRecord(
        setup_sha256=setup_sha256,
        inputs={
            "manifest": hashlib.sha256(manifest_bytes).hexdigest(),
            "query_snapshot": rows_sha256(records),
        },
        outputs={"column_refs": write_rows(rows, ColumnRef, out_dir / COLUMN_REFS_FILE)},
        counts=counts,
        gates={},
    )
