import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, parse_qs

# ==============================================================================
# POSTGRESQL DISCOVERY SQL
# ==============================================================================

PG_DISCOVERY_SQL = r"""
SELECT 
    current_database() AS database_name,
    n.nspname AS schema_name,
    c.relname AS object_name,
    CASE c.relkind
        WHEN 'r' THEN 'TABLE'
        WHEN 'p' THEN 'TABLE'
        WHEN 'v' THEN 'VIEW'
        WHEN 'm' THEN 'VIEW'
        WHEN 'f' THEN 'FOREIGN_TABLE'
        ELSE 'TABLE'
    END AS object_type,
    pg_get_viewdef(c.oid, true) AS definition
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
  AND c.relkind IN ('r', 'p', 'v', 'm')
UNION ALL
SELECT
    current_database() AS database_name,
    n.nspname AS schema_name,
    p.proname AS object_name,
    CASE p.prokind
        WHEN 'p' THEN 'PROCEDURE'
        WHEN 'f' THEN 'FUNCTION'
        WHEN 'a' THEN 'AGGREGATE'
        WHEN 'w' THEN 'WINDOW_FUNCTION'
        ELSE 'FUNCTION'
    END AS object_type,
    pg_get_functiondef(p.oid) AS definition
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
UNION ALL
SELECT
    current_database() AS database_name,
    n.nspname AS schema_name,
    t.tgname AS object_name,
    'TRIGGER' AS object_type,
    pg_get_triggerdef(t.oid) AS definition
FROM pg_trigger t
JOIN pg_class c ON c.oid = t.tgrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
  AND NOT t.tgisinternal;
"""

PG_COLUMN_SQL = r"""
SELECT 
    n.nspname AS schema_name,
    c.relname AS object_name,
    a.attname AS column_name,
    a.attnum AS column_id,
    format_type(a.atttypid, a.atttypmod) AS declared_data_type,
    t.typname AS system_data_type,
    (t.typtype = 'd' OR t.typtype = 'e') AS is_user_defined,
    information_schema._pg_char_max_length(a.atttypid, a.atttypmod) AS max_length,
    information_schema._pg_numeric_precision(a.atttypid, a.atttypmod) AS precision,
    information_schema._pg_numeric_scale(a.atttypid, a.atttypmod) AS scale,
    NOT a.attnotnull AS is_nullable,
    (a.attidentity != '' OR pg_get_expr(ad.adbin, ad.adrelid) LIKE 'nextval(%%') AS is_identity,
    (a.attgenerated != '') AS is_computed,
    pg_get_expr(ad.adbin, ad.adrelid) AS default_definition,
    coll.collname AS collation_name
FROM pg_attribute a
JOIN pg_class c ON c.oid = a.attrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_type t ON t.oid = a.atttypid
LEFT JOIN pg_attrdef ad ON ad.adrelid = a.attrelid AND ad.adnum = a.attnum
LEFT JOIN pg_collation coll ON coll.oid = a.attcollation
WHERE n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
  AND c.relkind IN ('r', 'p', 'v', 'm')
  AND a.attnum > 0
  AND NOT a.attisdropped
ORDER BY n.nspname, c.relname, a.attnum;
"""

PG_KEY_CONSTRAINT_SQL = r"""
SELECT 
    n.nspname AS schema_name,
    c.relname AS object_name,
    con.conname AS constraint_name,
    CASE con.contype
        WHEN 'p' THEN 'PRIMARY KEY'
        WHEN 'u' THEN 'UNIQUE'
        ELSE con.contype::text
    END AS constraint_type,
    pos.ordinal AS key_ordinal,
    a.attname AS column_name
FROM pg_constraint con
JOIN pg_class c ON c.oid = con.conrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
CROSS JOIN LATERAL unnest(con.conkey) WITH ORDINALITY AS pos(attnum, ordinal)
JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = pos.attnum
WHERE n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
  AND con.contype IN ('p', 'u')
ORDER BY n.nspname, c.relname, con.conname, pos.ordinal;
"""

PG_FOREIGN_KEY_SQL = r"""
SELECT 
    n.nspname AS schema_name,
    c.relname AS object_name,
    con.conname AS constraint_name,
    pos.ordinal AS ordinal,
    a.attname AS column_name,
    fn.nspname AS referenced_schema,
    fc.relname AS referenced_object,
    fa.attname AS referenced_column
FROM pg_constraint con
JOIN pg_class c ON c.oid = con.conrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_class fc ON fc.oid = con.confrelid
JOIN pg_namespace fn ON fn.oid = fc.relnamespace
CROSS JOIN LATERAL unnest(con.conkey) WITH ORDINALITY AS pos(attnum, ordinal)
JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = pos.attnum
CROSS JOIN LATERAL unnest(con.confkey) WITH ORDINALITY AS fpos(confattnum, forder)
JOIN pg_attribute fa ON fa.attrelid = fc.oid AND fa.attnum = fpos.confattnum AND pos.ordinal = fpos.forder
WHERE n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
  AND con.contype = 'f'
ORDER BY n.nspname, c.relname, con.conname, pos.ordinal;
"""

PG_TABLE_STATS_SQL = r"""
SELECT 
    schemaname AS schema_name,
    relname AS object_name,
    n_live_tup AS approx_row_count
FROM pg_stat_user_tables;
"""

PG_DEPENDENCY_SQL = r"""
SELECT 
    n1.nspname AS referencing_schema_name,
    c1.relname AS referencing_entity_name,
    NULL::text AS referenced_server_name,
    current_database() AS referenced_database_name,
    n2.nspname AS referenced_schema_name,
    c2.relname AS referenced_entity_name,
    NULL::text AS referenced_column_name,
    NULL::int AS referenced_minor_id,
    'LOCAL' AS dependency_scope,
    false AS is_schema_bound_reference,
    false AS is_caller_dependent,
    false AS is_ambiguous
FROM pg_depend d
JOIN pg_rewrite r ON r.oid = d.objid
JOIN pg_class c1 ON c1.oid = r.ev_class
JOIN pg_namespace n1 ON n1.oid = c1.relnamespace
JOIN pg_class c2 ON c2.oid = d.refobjid
JOIN pg_namespace n2 ON n2.oid = c2.relnamespace
WHERE n1.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
  AND n2.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
  AND c1.oid != c2.oid;
"""

# ==============================================================================
# SQL SERVER DISCOVERY SQL (Backwards Compatibility)
# ==============================================================================

DISCOVERY_SQL = r"""
SELECT DB_NAME() AS database_name, s.name AS schema_name, o.name AS object_name,
       CASE o.type WHEN 'U' THEN 'TABLE' WHEN 'V' THEN 'VIEW' WHEN 'P' THEN 'PROCEDURE'
                   WHEN 'FN' THEN 'FUNCTION' WHEN 'IF' THEN 'FUNCTION' WHEN 'TF' THEN 'FUNCTION'
                   WHEN 'TR' THEN 'TRIGGER' ELSE o.type_desc END AS object_type,
       m.definition
FROM sys.objects o
JOIN sys.schemas s ON s.schema_id=o.schema_id
LEFT JOIN sys.sql_modules m ON m.object_id=o.object_id
WHERE o.is_ms_shipped=0 AND o.type IN ('U','V','P','FN','IF','TF','TR');
"""
COLUMN_SQL = r"""
SELECT s.name schema_name,o.name object_name,c.name column_name,c.column_id,
       t.name declared_data_type, TYPE_NAME(c.system_type_id) system_data_type, t.is_user_defined,
       c.max_length,c.precision,c.scale,c.is_nullable,c.is_identity,c.is_computed,
       dc.definition default_definition, c.collation_name
FROM sys.objects o JOIN sys.schemas s ON o.schema_id=s.schema_id
JOIN sys.columns c ON c.object_id=o.object_id JOIN sys.types t ON c.user_type_id=t.user_type_id
LEFT JOIN sys.default_constraints dc ON c.default_object_id=dc.object_id
WHERE o.is_ms_shipped=0 AND o.type IN ('U','V');
"""
DEPENDENCY_SQL = r"""
SELECT
    d.referencing_id,
    OBJECT_SCHEMA_NAME(d.referencing_id) AS referencing_schema_name,
    OBJECT_NAME(d.referencing_id) AS referencing_entity_name,
    d.referenced_id,
    d.referenced_server_name,
    d.referenced_database_name,
    d.referenced_schema_name,
    d.referenced_entity_name,
    d.referenced_minor_id,
    c.name AS referenced_column_name,
    CASE
        WHEN d.referenced_server_name IS NOT NULL THEN 'EXTERNAL_SERVER'
        WHEN d.referenced_database_name IS NOT NULL AND d.referenced_database_name <> DB_NAME() THEN 'CROSS_DATABASE'
        WHEN d.referenced_schema_name IS NOT NULL AND d.referenced_schema_name <> OBJECT_SCHEMA_NAME(d.referencing_id) THEN 'CROSS_SCHEMA'
        ELSE 'LOCAL'
    END AS dependency_scope,
    d.is_schema_bound_reference,
    d.is_caller_dependent,
    d.is_ambiguous
FROM sys.sql_expression_dependencies AS d
LEFT JOIN sys.columns AS c
    ON c.object_id = d.referenced_id
   AND c.column_id = d.referenced_minor_id
WHERE d.referenced_entity_name IS NOT NULL;
"""
KEY_CONSTRAINT_SQL = r"""
SELECT s.name AS schema_name, o.name AS object_name, kc.name AS constraint_name,
       kc.type_desc AS constraint_type, ic.key_ordinal, c.name AS column_name
FROM sys.key_constraints kc
JOIN sys.objects o ON o.object_id=kc.parent_object_id
JOIN sys.schemas s ON s.schema_id=o.schema_id
JOIN sys.index_columns ic ON ic.object_id=o.object_id AND ic.index_id=kc.unique_index_id
JOIN sys.columns c ON c.object_id=o.object_id AND c.column_id=ic.column_id
WHERE o.is_ms_shipped=0 AND kc.type IN ('PK','UQ')
ORDER BY s.name,o.name,kc.name,ic.key_ordinal;
"""
FOREIGN_KEY_SQL = r"""
SELECT ps.name AS schema_name, po.name AS object_name, fk.name AS constraint_name,
       fkc.constraint_column_id AS ordinal, pc.name AS column_name,
       rs.name AS referenced_schema, ro.name AS referenced_object, rc.name AS referenced_column
FROM sys.foreign_keys fk
JOIN sys.foreign_key_columns fkc ON fkc.constraint_object_id=fk.object_id
JOIN sys.objects po ON po.object_id=fk.parent_object_id
JOIN sys.schemas ps ON ps.schema_id=po.schema_id
JOIN sys.columns pc ON pc.object_id=po.object_id AND pc.column_id=fkc.parent_column_id
JOIN sys.objects ro ON ro.object_id=fk.referenced_object_id
JOIN sys.schemas rs ON rs.schema_id=ro.schema_id
JOIN sys.columns rc ON rc.object_id=ro.object_id AND rc.column_id=fkc.referenced_column_id
WHERE po.is_ms_shipped=0
ORDER BY ps.name,po.name,fk.name,fkc.constraint_column_id;
"""
TABLE_STATS_SQL = r"""
SELECT s.name AS schema_name,o.name AS object_name,
       SUM(CASE WHEN p.index_id IN (0,1) THEN p.row_count ELSE 0 END) AS approx_row_count
FROM sys.objects o
JOIN sys.schemas s ON s.schema_id=o.schema_id
JOIN sys.dm_db_partition_stats p ON p.object_id=o.object_id
WHERE o.is_ms_shipped=0 AND o.type='U'
GROUP BY s.name,o.name;
"""
PARAMETER_SQL = r"""
SELECT s.name AS schema_name,o.name AS object_name,p.name AS parameter_name,p.parameter_id,
       t.name AS data_type,p.max_length,p.precision,p.scale,p.is_output
FROM sys.objects o
JOIN sys.schemas s ON s.schema_id=o.schema_id
JOIN sys.parameters p ON p.object_id=o.object_id
JOIN sys.types t ON t.user_type_id=p.user_type_id
WHERE o.is_ms_shipped=0 AND o.type IN ('P','FN','IF','TF');
"""

# ==============================================================================
# CONNECTION DIAGNOSTICS & HELPERS
# ==============================================================================

def connection_diagnostic(error: Exception) -> str:
    message = str(error).lower()
    if "password authentication failed" in message or "28p01" in message:
        return "AUTHENTICATION_FAILED: Verify PostgreSQL username and password."
    if "database" in message and ("does not exist" in message or "3d000" in message):
        return "DATABASE_ACCESS: Verify the PostgreSQL database name exists and is accessible."
    if "could not connect to server" in message or "connection refused" in message or "08001" in message:
        return "NETWORK_UNREACHABLE: The backend could not reach PostgreSQL host/port. Verify network, host, and port 5432."
    if "ssl" in message or "certificate" in message:
        return "TLS_ERROR: Verify PostgreSQL SSL configuration and sslmode setting."
    if "4060" in message or "cannot open database" in message:
        return "DATABASE_ACCESS: Verify the database name and grant connector read and metadata permissions."
    if "28000" in message or "login failed" in message or "18456" in message:
        return "AUTHENTICATION_FAILED: Verify database credentials."
    if "im002" in message or "driver" in message and ("not found" in message or "can't open" in message):
        return "DRIVER_MISSING: Install required driver."
    return f"SOURCE_OPERATION_FAILED: {str(error)}"


def parse_postgres_conn(conn_info: Any) -> dict:
    if isinstance(conn_info, dict):
        return conn_info
    s = str(conn_info).strip()
    if s.startswith("postgresql://") or s.startswith("postgres://"):
        u = urlparse(s)
        params = parse_qs(u.query)
        return {
            "host": u.hostname or "localhost",
            "port": u.port or 5432,
            "dbname": u.path.lstrip("/") if u.path else "postgres",
            "user": u.username or "postgres",
            "password": u.password or "",
            "sslmode": params.get("sslmode", ["prefer"])[0],
        }
    parts = s.split()
    out = {}
    for p in parts:
        if "=" in p:
            k, v = p.split("=", 1)
            out[k.strip()] = v.strip()
    return out


# ==============================================================================
# POSTGRESQL DISCOVERY IMPLEMENTATION
# ==============================================================================

def test_postgres_connection(conn_info: Any) -> dict[str, Any]:
    try:
        import psycopg2
    except ImportError:
        try:
            import psycopg as psycopg2
        except ImportError as e:
            raise RuntimeError("psycopg2-binary or psycopg is required for PostgreSQL connectivity") from e
    
    cfg = parse_postgres_conn(conn_info)
    try:
        conn = psycopg2.connect(**cfg)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT current_database(), inet_server_addr()::text, version()")
                row = cur.fetchone()
                db_name = row[0] if row else cfg.get("dbname", "postgres")
                server_addr = row[1] if row and row[1] else cfg.get("host", "localhost")
                version = row[2] if row else "PostgreSQL"
                return {
                    "ok": True,
                    "server": server_addr or "localhost",
                    "database": db_name,
                    "product_version": version,
                }
        finally:
            conn.close()
    except Exception as e:
        raise RuntimeError(connection_diagnostic(e)) from e


def discover_postgres(conn_info: Any) -> dict[str, Any]:
    try:
        import psycopg2
    except ImportError:
        try:
            import psycopg as psycopg2
        except ImportError as e:
            raise RuntimeError("psycopg2-binary or psycopg is required for PostgreSQL discovery") from e
    
    cfg = parse_postgres_conn(conn_info)
    try:
        conn = psycopg2.connect(**cfg)
        try:
            with conn.cursor() as cur:
                # 1. Objects
                cur.execute(PG_DISCOVERY_SQL)
                obj_rows = cur.fetchall()
                objs = [
                    {
                        "database_name": r[0], "schema_name": r[1],
                        "object_name": r[2], "object_type": r[3], "definition": r[4]
                    }
                    for r in obj_rows
                ]

                # 2. Columns
                cur.execute(PG_COLUMN_SQL)
                col_rows = cur.fetchall()
                cols = [
                    {
                        "schema_name": r[0], "object_name": r[1], "column_name": r[2],
                        "column_id": r[3], "declared_data_type": r[4], "system_data_type": r[5],
                        "is_user_defined": r[6], "max_length": r[7], "precision": r[8],
                        "scale": r[9], "is_nullable": r[10], "is_identity": r[11],
                        "is_computed": r[12], "default_definition": r[13], "collation_name": r[14]
                    }
                    for r in col_rows
                ]

                # 3. Dependencies
                try:
                    cur.execute(PG_DEPENDENCY_SQL)
                    dep_rows = cur.fetchall()
                    deps = [
                        {
                            "referencing_schema_name": r[0], "referencing_entity_name": r[1],
                            "referenced_server_name": r[2], "referenced_database_name": r[3],
                            "referenced_schema_name": r[4], "referenced_entity_name": r[5],
                            "referenced_column_name": r[6], "referenced_minor_id": r[7],
                            "dependency_scope": r[8], "is_schema_bound_reference": r[9],
                            "is_caller_dependent": r[10], "is_ambiguous": r[11]
                        }
                        for r in dep_rows
                    ]
                except Exception:
                    deps = []

                # 4. Key Constraints
                try:
                    cur.execute(PG_KEY_CONSTRAINT_SQL)
                    kc_rows = cur.fetchall()
                    key_constraints = [
                        {
                            "schema_name": r[0], "object_name": r[1], "constraint_name": r[2],
                            "constraint_type": r[3], "key_ordinal": r[4], "column_name": r[5]
                        }
                        for r in kc_rows
                    ]
                except Exception:
                    key_constraints = []

                # 5. Foreign Keys
                try:
                    cur.execute(PG_FOREIGN_KEY_SQL)
                    fk_rows = cur.fetchall()
                    foreign_keys = [
                        {
                            "schema_name": r[0], "object_name": r[1], "constraint_name": r[2],
                            "ordinal": r[3], "column_name": r[4], "referenced_schema": r[5],
                            "referenced_object": r[6], "referenced_column": r[7]
                        }
                        for r in fk_rows
                    ]
                except Exception:
                    foreign_keys = []

                # 6. Table Stats
                try:
                    cur.execute(PG_TABLE_STATS_SQL)
                    stat_rows = cur.fetchall()
                    table_stats = [
                        {"schema_name": r[0], "object_name": r[1], "approx_row_count": r[2]}
                        for r in stat_rows
                    ]
                except Exception:
                    table_stats = []

            by: dict[tuple[str, str], list[dict[str, Any]]] = {(r["schema_name"], r["object_name"]): [] for r in objs}
            for c in cols:
                by.setdefault((c["schema_name"], c["object_name"]), []).append({
                    "name": c["column_name"],
                    "ordinal": c["column_id"],
                    "type": c["declared_data_type"] or c["system_data_type"],
                    "declared_type": c["declared_data_type"],
                    "system_type": c["system_data_type"],
                    "is_user_defined": bool(c["is_user_defined"]),
                    "max_length": c["max_length"],
                    "precision": c["precision"],
                    "scale": c["scale"],
                    "nullable": bool(c["is_nullable"]),
                    "identity": bool(c["is_identity"]),
                    "computed": bool(c["is_computed"]),
                    "default": c["default_definition"],
                    "collation": c["collation_name"],
                })

            dep_by: dict[tuple[str, str], list[dict[str, Any]]] = {}
            for d in deps:
                dep_by.setdefault((d["referencing_schema_name"], d["referencing_entity_name"]), []).append({
                    "server": d["referenced_server_name"],
                    "database": d["referenced_database_name"],
                    "schema": d["referenced_schema_name"],
                    "object": d["referenced_entity_name"],
                    "column": d["referenced_column_name"],
                    "referenced_minor_id": d["referenced_minor_id"],
                    "type": d["dependency_scope"],
                    "is_schema_bound_reference": bool(d["is_schema_bound_reference"]),
                    "is_caller_dependent": bool(d["is_caller_dependent"]),
                    "is_ambiguous": bool(d["is_ambiguous"]),
                })

            constraint_by: dict[tuple[str, str], list[dict[str, Any]]] = {}
            grouped_keys: dict[tuple[str, str, str], dict[str, Any]] = {}
            for k in key_constraints:
                key = (k["schema_name"], k["object_name"], k["constraint_name"])
                row = grouped_keys.setdefault(key, {
                    "name": k["constraint_name"],
                    "type": "PRIMARY_KEY" if str(k["constraint_type"]).upper().startswith("PRIMARY") else "UNIQUE",
                    "columns": [],
                })
                row["columns"].append(k["column_name"])
            for (sch, obj, _), row in grouped_keys.items():
                constraint_by.setdefault((sch, obj), []).append(row)

            grouped_fks: dict[tuple[str, str, str], dict[str, Any]] = {}
            for f in foreign_keys:
                key = (f["schema_name"], f["object_name"], f["constraint_name"])
                row = grouped_fks.setdefault(key, {
                    "name": f["constraint_name"], "type": "FOREIGN_KEY", "columns": [],
                    "referenced_schema": f["referenced_schema"], "referenced_object": f["referenced_object"],
                    "referenced_columns": [],
                })
                row["columns"].append(f["column_name"])
                row["referenced_columns"].append(f["referenced_column"])
            for (sch, obj, _), row in grouped_fks.items():
                constraint_by.setdefault((sch, obj), []).append(row)

            stats_by = {(r["schema_name"], r["object_name"]): int(r["approx_row_count"] or 0) for r in table_stats}

            return {
                "database": objs[0]["database_name"] if objs else cfg.get("dbname", ""),
                "objects": [{
                    "database": r["database_name"],
                    "schema": r["schema_name"],
                    "name": r["object_name"],
                    "type": r["object_type"],
                    "definition": r["definition"],
                    "columns": by.get((r["schema_name"], r["object_name"]), []),
                    "dependencies": dep_by.get((r["schema_name"], r["object_name"]), []),
                    "parameters": [],
                    "constraints": constraint_by.get((r["schema_name"], r["object_name"]), []),
                    "approx_row_count": stats_by.get((r["schema_name"], r["object_name"]))
                } for r in objs]
            }
        finally:
            conn.close()
    except Exception as e:
        raise RuntimeError(connection_diagnostic(e)) from e


# ==============================================================================
# SQL SERVER DISCOVERY WRAPPER (Backwards Compatibility)
# ==============================================================================

def test_sqlserver_connection(connection_string: str) -> dict[str, Any]:
    try:
        import pyodbc
    except Exception as e:
        raise RuntimeError("pyodbc is required for live SQL Server connectivity") from e
    try:
        with pyodbc.connect(connection_string, timeout=10) as conn:
            cur = conn.cursor()
            row = cur.execute("SELECT @@SERVERNAME AS server_name, DB_NAME() AS database_name, CAST(SERVERPROPERTY('ProductVersion') AS varchar(128)) AS product_version").fetchone()
            return {"ok": True, "server": row.server_name, "database": row.database_name, "product_version": row.product_version}
    except Exception as e:
        raise RuntimeError(connection_diagnostic(e)) from e


def discover_sqlserver(connection_string: str) -> dict[str, Any]:
    try:
        import pyodbc
    except Exception as e:
        raise RuntimeError("pyodbc is required for live SQL Server discovery") from e
    try:
        with pyodbc.connect(connection_string, timeout=20) as conn:
            cur = conn.cursor()
            objs = cur.execute(DISCOVERY_SQL).fetchall()
            cols = cur.execute(COLUMN_SQL).fetchall()
            deps = cur.execute(DEPENDENCY_SQL).fetchall()
            params = cur.execute(PARAMETER_SQL).fetchall()
            try:
                key_constraints = cur.execute(KEY_CONSTRAINT_SQL).fetchall()
            except Exception:
                key_constraints = []
            try:
                foreign_keys = cur.execute(FOREIGN_KEY_SQL).fetchall()
            except Exception:
                foreign_keys = []
            try:
                table_stats = cur.execute(TABLE_STATS_SQL).fetchall()
            except Exception:
                table_stats = []
            by = {(r.schema_name, r.object_name): [] for r in objs}
            for c in cols:
                by.setdefault((c.schema_name, c.object_name), []).append({
                    "name": c.column_name, "ordinal": c.column_id,
                    "type": (c.system_data_type if bool(c.is_user_defined) and c.system_data_type else c.declared_data_type),
                    "declared_type": c.declared_data_type, "system_type": c.system_data_type, "is_user_defined": bool(c.is_user_defined),
                    "max_length": c.max_length, "precision": c.precision, "scale": c.scale,
                    "nullable": bool(c.is_nullable), "identity": bool(c.is_identity),
                    "computed": bool(c.is_computed), "default": c.default_definition,
                    "collation": c.collation_name
                })
            dep_by: dict[tuple[str, str], list[dict[str, Any]]] = {}
            for d in deps:
                dep_by.setdefault((d.referencing_schema_name, d.referencing_entity_name), []).append({
                    "server": d.referenced_server_name,
                    "database": d.referenced_database_name,
                    "schema": d.referenced_schema_name,
                    "object": d.referenced_entity_name,
                    "column": d.referenced_column_name,
                    "referenced_minor_id": d.referenced_minor_id,
                    "type": d.dependency_scope,
                    "is_schema_bound_reference": bool(d.is_schema_bound_reference),
                    "is_caller_dependent": bool(d.is_caller_dependent),
                    "is_ambiguous": bool(d.is_ambiguous),
                })
            par_by: dict[tuple[str, str], list[dict[str, Any]]] = {}
            for p in params:
                par_by.setdefault((p.schema_name, p.object_name), []).append({
                    "name": p.parameter_name, "ordinal": p.parameter_id, "type": p.data_type,
                    "max_length": p.max_length, "precision": p.precision, "scale": p.scale,
                    "is_output": bool(p.is_output)
                })
            constraint_by: dict[tuple[str, str], list[dict[str, Any]]] = {}
            grouped_keys: dict[tuple[str, str, str], dict[str, Any]] = {}
            for k in key_constraints:
                key = (k.schema_name, k.object_name, k.constraint_name)
                row = grouped_keys.setdefault(key, {
                    "name": k.constraint_name,
                    "type": "PRIMARY_KEY" if str(k.constraint_type).upper().startswith("PRIMARY") else "UNIQUE",
                    "columns": [],
                })
                row["columns"].append(k.column_name)
            for (sch, obj, _), row in grouped_keys.items():
                constraint_by.setdefault((sch, obj), []).append(row)
            grouped_fks: dict[tuple[str, str, str], dict[str, Any]] = {}
            for f in foreign_keys:
                key = (f.schema_name, f.object_name, f.constraint_name)
                row = grouped_fks.setdefault(key, {
                    "name": f.constraint_name, "type": "FOREIGN_KEY", "columns": [],
                    "referenced_schema": f.referenced_schema, "referenced_object": f.referenced_object,
                    "referenced_columns": [],
                })
                row["columns"].append(f.column_name)
                row["referenced_columns"].append(f.referenced_column)
            for (sch, obj, _), row in grouped_fks.items():
                constraint_by.setdefault((sch, obj), []).append(row)
            stats_by = {(r.schema_name, r.object_name): int(r.approx_row_count or 0) for r in table_stats}
            return {
                "database": objs[0].database_name if objs else "",
                "objects": [{
                    "database": r.database_name, "schema": r.schema_name, "name": r.object_name,
                    "type": r.object_type, "definition": r.definition,
                    "columns": by.get((r.schema_name, r.object_name), []),
                    "dependencies": dep_by.get((r.schema_name, r.object_name), []),
                    "parameters": par_by.get((r.schema_name, r.object_name), []),
                    "constraints": constraint_by.get((r.schema_name, r.object_name), []),
                    "approx_row_count": stats_by.get((r.schema_name, r.object_name))
                } for r in objs]
            }
    except Exception as e:
        raise RuntimeError(connection_diagnostic(e)) from e


def test_source_connection(conn_info: Any, source_type: str = "POSTGRESQL") -> dict[str, Any]:
    if source_type.upper() == "POSTGRESQL":
        return test_postgres_connection(conn_info)
    return test_sqlserver_connection(conn_info)


def discover_source(conn_info: Any, source_type: str = "POSTGRESQL") -> dict[str, Any]:
    if source_type.upper() == "POSTGRESQL":
        return discover_postgres(conn_info)
    return discover_sqlserver(conn_info)

