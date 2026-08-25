"""Azure SQL Server connection module for dbo.CycleChangeLog."""
import os
import sys
import pandas as pd

TABLE = "dbo.CycleChangeLog"


def credentials():
    env = {k: os.environ.get(k) for k in
           ("SQL_SERVER", "SQL_DATABASE", "SQL_USER", "SQL_PASSWORD")}
    if all(env.values()):
        return (env["SQL_SERVER"], env["SQL_DATABASE"], env["SQL_USER"], env["SQL_PASSWORD"])

    # Fallback to local Van Dyk Tools config if on Windows local machine
    from pathlib import Path
    import json
    config_path = Path(r"G:\After Sales Team\Van Dyk Tools\config\config.json")
    if config_path.exists():
        try:
            cfg = json.loads(config_path.read_text(encoding="utf-8-sig"))
            for section in ("blobcheck", "subassembly", "sort_folder_sync"):
                s = cfg.get(section, {})
                if s.get("sql_server") and s.get("sql_password"):
                    return (s["sql_server"], s.get("sql_db") or s.get("sql_database"),
                            s["sql_user"], s["sql_password"])
        except Exception:
            pass

    return None


def connect():
    creds = credentials()
    if not creds:
        return None

    try:
        import pyodbc
    except ImportError:
        print("pyodbc is not installed")
        return None

    server, database, user, password = creds
    drivers = [d for d in pyodbc.drivers() if "SQL Server" in d]
    if not drivers:
        print("No SQL Server ODBC driver found")
        return None

    drv = drivers[-1]
    try:
        cn = pyodbc.connect(
            f"DRIVER={{{drv}}};SERVER={server};DATABASE={database};UID={user};PWD={password};"
            f"Encrypt=yes;TrustServerCertificate=yes;Connection Timeout=30", timeout=30)
        print(f"Connected to {server} / {database} using {drv}")
        return cn
    except Exception as e:
        print(f"SQL Server connection warning: {e}")
        return None


def fetch_changes_sql():
    """Fetch all rows from dbo.CycleChangeLog."""
    cn = connect()
    if cn is None:
        return None
    try:
        sql = "SELECT Part, Description, ItemClass, Supplier, CycleFrom, CycleTo, ReasonWord, ReasonDetail, ApprovedBy, DecidedOn, SourceFile, SourceSheet, OnHand, StockValue, ReorderPoint, SetReorderTo, LiveCycle, AppliedInAcumatica, AppliedAt, LastCheckedAt, UpdatedAt FROM dbo.CycleChangeLog"
        df = pd.read_sql(sql, cn)
        cn.close()
        return df
    except Exception as e:
        print(f"Failed to query SQL Server: {e}")
        try:
            cn.close()
        except Exception:
            pass
        return None


def update_live_status_sql(df):
    """Batch update live cycle status into dbo.CycleChangeLog on Azure SQL Server."""
    cn = connect()
    if cn is None:
        return False
    try:
        cur = cn.cursor()
        UPDATE_SQL = f"""
        UPDATE {TABLE}
           SET LiveCycle = ?,
               AppliedInAcumatica = ?,
               LastCheckedAt = ?,
               AppliedAt = CASE WHEN AppliedAt IS NULL AND ? = 'YES' THEN SYSUTCDATETIME() ELSE AppliedAt END,
               UpdatedAt = SYSUTCDATETIME()
         WHERE Part = ? AND CycleTo = ? AND SourceSheet = ?;
        """
        updates = []
        for r in df.itertuples(index=False):
            updates.append([
                getattr(r, "LiveCycle", None),
                getattr(r, "AppliedInAcumatica", None),
                getattr(r, "LastCheckedAt", None),
                getattr(r, "AppliedInAcumatica", None),
                getattr(r, "Part", None),
                getattr(r, "CycleTo", None),
                getattr(r, "SourceSheet", None)
            ])

        chunk_size = 100
        for i in range(0, len(updates), chunk_size):
            chunk = updates[i:i+chunk_size]
            cur.executemany(UPDATE_SQL, chunk)
            cn.commit()

        cn.close()
        print(f"Updated {len(updates)} records in {TABLE}")
        return True
    except Exception as e:
        print(f"Failed to update SQL Server: {e}")
        try:
            cn.close()
        except Exception:
            pass
        return False
