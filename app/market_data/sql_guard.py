"""Validação e execução segura do SQL gerado pelo LLM (Text-to-Query).

Duas camadas independentes — o SQL do modelo nunca é executado sem passar pelas duas:

1. **Parser (sqlglot, dialeto SQLite)** antes da execução:
   - exatamente UM comando, e ele precisa ser ``SELECT`` (CTEs ``WITH`` permitidas);
   - nenhum nó de escrita/DDL/controle (INSERT, UPDATE, DELETE, DROP, CREATE, ALTER, PRAGMA,
     ATTACH, transações, comandos não reconhecidos);
   - só tabelas/views e colunas da lista permitida (``ALLOWED_COLUMNS``), mais aliases e CTEs
     da própria consulta;
   - funções só da lista permitida;
   - ``LIMIT`` obrigatório, no máximo ``max_rows`` (acrescentado ou reduzido);
   - literais viram parâmetros (``?``) ligados na execução.
2. **SQLite** na execução:
   - conexão ``mode=ro`` (o próprio SQLite recusa escrita);
   - ``set_authorizer``: só leitura de tabelas/colunas permitidas e funções permitidas;
   - ``set_progress_handler``: interrompe a consulta que passar do tempo limite.

Mensagens de erro são genéricas: não expõem caminho do arquivo nem detalhes internos.
"""

import sqlite3
import time
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from app.market_data.store import VIEWS

ALLOWED_FUNCTIONS = frozenset({
    "abs", "avg", "coalesce", "count", "date", "datetime", "group_concat", "ifnull", "iif", "instr",
    "julianday", "length", "lower", "max", "min", "nullif", "printf", "round", "strftime", "substr",
    "substring", "sum", "total", "trim", "upper", "replace", "unixepoch", "time",
})

# Nós que nunca podem aparecer, em qualquer posição da árvore.
FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = tuple(
    getattr(exp, name)
    for name in (
        "Insert", "Update", "Delete", "Drop", "Create", "Alter", "Command", "Pragma", "Attach",
        "Detach", "Merge", "TruncateTable", "Transaction", "Commit", "Rollback", "Set", "Use",
        "Copy", "Into", "LoadData",
    )
    if hasattr(exp, name)
)


class UnsafeQueryError(ValueError):
    """Consulta recusada antes da execução (insegura, inválida ou fora do schema)."""


class QueryExecutionError(RuntimeError):
    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class ValidatedQuery:
    sql: str  # SQL normalizado, com placeholders
    params: tuple
    tables: frozenset[str]
    columns: frozenset[str]  # colunas referenciadas (sem aliases)
    filter_columns: frozenset[str] = field(default_factory=frozenset)  # colunas em WHERE/HAVING
    limit: int = 0


def validate_sql(sql: str, allowed: dict[str, frozenset[str]], max_rows: int) -> ValidatedQuery:
    text = (sql or "").strip().rstrip(";").strip()
    if not text:
        raise UnsafeQueryError("consulta vazia")
    if len(text) > 4000:
        raise UnsafeQueryError("consulta longa demais")
    try:
        statements = [s for s in sqlglot.parse(text, read="sqlite") if s is not None]
    except ParseError as exc:
        raise UnsafeQueryError(f"SQL inválido: {str(exc).splitlines()[0][:160]}") from exc
    if len(statements) != 1:
        raise UnsafeQueryError("apenas um comando SQL por consulta")
    tree = statements[0]
    if not isinstance(tree, exp.Select):
        raise UnsafeQueryError(f"apenas SELECT é permitido (recebido: {tree.key.upper()})")
    for node in tree.walk():
        if isinstance(node, FORBIDDEN_NODES):
            raise UnsafeQueryError(f"operação não permitida: {node.key.upper()}")

    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    tables = set()
    for table in tree.find_all(exp.Table):
        name = table.name.lower()
        if table.args.get("db") or table.args.get("catalog"):
            raise UnsafeQueryError("referência a outro banco não é permitida")
        if name in cte_names:
            continue
        if name not in allowed:
            raise UnsafeQueryError(f"tabela não permitida: {name}")
        tables.add(name)
    if not tables:
        raise UnsafeQueryError("a consulta precisa ler ao menos uma tabela permitida")

    aliases = {a.alias.lower() for a in tree.find_all(exp.Alias)}
    for cte in tree.find_all(exp.CTE):  # colunas nomeadas da CTE: WITH c(x) AS (...)
        alias = cte.args.get("alias")
        aliases |= {c.name.lower() for c in (alias.columns if alias else [])}
    allowed_columns = set().union(*(allowed[t] for t in tables))
    columns = set()
    for column in tree.find_all(exp.Column):
        name = column.name.lower()
        if name in allowed_columns:
            columns.add(name)
        elif name not in aliases:
            raise UnsafeQueryError(f"coluna não permitida ou inexistente: {name}")

    # Funções desconhecidas do sqlglot são barradas aqui; as conhecidas passam pelo authorizer.
    for func in tree.find_all(exp.Anonymous):
        if str(func.name).lower() not in ALLOWED_FUNCTIONS:
            raise UnsafeQueryError(f"função não permitida: {str(func.name).lower()}")

    filter_columns = {
        c.name.lower()
        for clause in (tree.args.get("where"), tree.args.get("having"))
        if clause is not None
        for c in clause.find_all(exp.Column)
        if c.name.lower() in allowed_columns
    }

    limit_node = tree.args.get("limit")
    limit = max_rows
    if limit_node is not None:
        value = limit_node.expression
        if not isinstance(value, exp.Literal) or not value.is_int:
            raise UnsafeQueryError("LIMIT precisa ser um número inteiro")
        limit = max(1, min(int(value.this), max_rows))
    tree = tree.limit(limit, copy=False) if limit_node is None else tree
    if limit_node is not None:
        tree.args["limit"].set("expression", exp.Literal.number(limit))

    # Literais viram parâmetros ligados na execução (o texto final não carrega valores do LLM).
    params: list = []
    for literal in list(tree.find_all(exp.Literal)):
        if literal.parent is tree.args.get("limit"):
            continue
        if isinstance(literal.parent, (exp.Ordered, exp.Group)):
            continue  # ORDER BY 1 / GROUP BY 1 são posições, não valores
        params.append(literal.this if literal.is_string else (int(literal.this) if literal.is_int else float(literal.this)))
        literal.replace(exp.Placeholder())
    return ValidatedQuery(
        sql=tree.sql(dialect="sqlite"),
        params=tuple(params),
        tables=frozenset(tables),
        columns=frozenset(columns),
        filter_columns=frozenset(filter_columns),
        limit=limit,
    )


def _authorizer(allowed: dict[str, frozenset[str]]):
    readable = {(t, c) for t, cols in allowed.items() for c in cols}
    # Tabelas-base lidas por dentro das views permitidas (latest_snapshots, snapshot_history).
    views = set(allowed) & VIEWS
    base_tables = set(allowed) - views

    def authorize(action: int, arg1: str | None, arg2: str | None, db: str | None, origin: str | None) -> int:
        # SELECT e CTE recursiva (WITH RECURSIVE) são só leitura; o tempo limite cobre recursão longa.
        if action in (sqlite3.SQLITE_SELECT, getattr(sqlite3, "SQLITE_RECURSIVE", 33)):
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ:
            table, column = (arg1 or "").lower(), (arg2 or "").lower()
            if db not in (None, "main"):
                return sqlite3.SQLITE_DENY
            if (table, column) in readable or (origin in views and table in base_tables):
                return sqlite3.SQLITE_OK
            if column == "" and table in allowed:  # COUNT(*) e afins: leitura sem coluna
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_FUNCTION:
            return sqlite3.SQLITE_OK if (arg2 or "").lower() in ALLOWED_FUNCTIONS else sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_DENY

    return authorize


def execute_query(
    conn: sqlite3.Connection,
    query: ValidatedQuery,
    allowed: dict[str, frozenset[str]],
    timeout_s: float,
) -> tuple[list[str], list[dict]]:
    deadline = time.monotonic() + timeout_s
    conn.set_authorizer(_authorizer(allowed))
    # Chamado a cada N instruções da VM do SQLite; retorno != 0 interrompe a consulta.
    conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 1000)
    try:
        cursor = conn.execute(query.sql, query.params)
        rows = cursor.fetchmany(query.limit)
        columns = [d[0] for d in cursor.description or []]
    except sqlite3.DatabaseError as exc:
        message = str(exc).lower()
        if "interrupted" in message:
            raise QueryExecutionError("timeout", f"a consulta excedeu {timeout_s:g} s") from exc
        if "not authorized" in message or "prohibited" in message:
            raise QueryExecutionError("invalid_query", "a consulta tentou acessar algo não permitido") from exc
        if isinstance(exc, sqlite3.OperationalError):
            raise QueryExecutionError("invalid_query", "o banco recusou a consulta (SQL inválido para o schema)") from exc
        raise QueryExecutionError("execution_error", "falha ao executar a consulta no banco de mercado") from exc
    finally:
        conn.set_authorizer(None)
        conn.set_progress_handler(None, 0)
    return columns, [dict(zip(columns, tuple(row))) for row in rows]
