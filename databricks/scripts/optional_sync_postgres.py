"""Optional Phase-5 sync: replicate dat_hack_rb06.rbe Gold tables into Azure PostgreSQL."""

from __future__ import annotations

import argparse
import io
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from databricks.src.scoring import ALERTS_INSERT_COLS, SERIES_SUMMARY_COLS  # noqa: E402

MODEL_RUNS_COLS = [
    "run_id", "model_version", "is_active", "created_at", "eval_start_date", "eval_end_date",
    "total_candidate_rows", "total_alerts", "total_caidas", "total_subidas",
    "f1_synthetic", "precision_synthetic", "recall_synthetic", "precision_at_100_synthetic", "params_json",
]

RESTAURANTS_DIM_COLS = [
    "restaurante_id", "marca", "pais", "comunidad_autonoma", "provincia", "provincia_inferida",
    "is_orphan_master", "tipologia", "tamano", "ubicacion", "imagen", "terraza",
    "tiene_autoking", "tiene_domicilio", "distancia_reparto", "estado",
    "primera_venta", "ultima_venta", "mediana_ventas_diaria_eur", "total_alertas",
]


def get_pg_connection():
    """Connect to Azure PostgreSQL using .env password or Entra ID access token."""
    import psycopg2

    host = os.environ.get("PGHOST", "dat-hack-rb06-pgsql.postgres.database.azure.com")
    port = int(os.environ.get("PGPORT", "5432"))
    dbname = os.environ.get("PGDATABASE", "postgres")
    user = os.environ.get("PGUSER", "dat_hack_rb06_dbkg")
    password = os.environ.get("PGPASSWORD")
    if not password:
        password = subprocess.check_output(
            ["az", "account", "get-access-token", "--resource-type", "oss-rdbms", "--query", "accessToken", "-o", "tsv"],
            text=True,
        ).strip()
    return psycopg2.connect(host=host, port=port, dbname=dbname, user=user, password=password, sslmode="require")


def bulk_replace_table(
    conn,
    schema_table: str,
    df: pd.DataFrame,
    columns: list[str],
    cascade: bool = False,
    truncate: bool = True,
    force_not_null: tuple[str, ...] = (),
) -> None:
    """Atomically truncate and COPY DataFrame into PostgreSQL preserving views and indexes."""
    buf = io.StringIO()
    df[columns].to_csv(buf, index=False, header=False, na_rep="\\N")
    buf.seek(0)
    trunc = f"TRUNCATE TABLE {schema_table} RESTART IDENTITY {'CASCADE' if cascade else ''};"
    cols_sql = ", ".join(columns)
    fnn_sql = f", FORCE_NOT_NULL ({', '.join(force_not_null)})" if force_not_null else ""
    copy_sql = f"COPY {schema_table} ({cols_sql}) FROM STDIN WITH (FORMAT csv, NULL '\\N'{fnn_sql})"
    with conn.cursor() as cur:
        if truncate:
            cur.execute(trunc)
        cur.copy_expert(copy_sql, buf)
    conn.commit()


def apply_schema(conn, schema_path: Path) -> None:
    """Apply idempotent sql/schema.sql DDL."""
    ddl = Path(schema_path).read_text(encoding="utf-8")
    with conn.cursor() as cur:
        cur.execute(ddl)
    conn.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description="Optional standby sync of Gold Parquets to Azure PostgreSQL.")
    parser.add_argument("--gold-dir", type=Path, default=REPO_ROOT / "outputs")
    parser.add_argument("--apply-schema", action="store_true")
    args = parser.parse_args()

    gold_dir = args.gold_dir
    runs = pd.read_parquet(gold_dir / "gold_model_runs.parquet")
    dim = pd.read_parquet(gold_dir / "gold_restaurants_dim.parquet")
    series = pd.read_parquet(gold_dir / "gold_series_summary.parquet")
    alerts = pd.read_parquet(gold_dir / "gold_alerts_prioritized.parquet")

    conn = get_pg_connection()
    try:
        if args.apply_schema:
            apply_schema(conn, REPO_ROOT / "sql" / "schema.sql")
        # Clear all 4 tables atomically preserving views, then COPY in strict parent->child FK order
        with conn.cursor() as cur:
            cur.execute(
                "TRUNCATE TABLE rbe.alerts_prioritized, rbe.series_summary, rbe.restaurants_dim, rbe.model_runs RESTART IDENTITY;"
            )
        conn.commit()
        bulk_replace_table(conn, "rbe.model_runs", runs, MODEL_RUNS_COLS, truncate=False)
        bulk_replace_table(conn, "rbe.restaurants_dim", dim, RESTAURANTS_DIM_COLS, truncate=False)
        bulk_replace_table(
            conn,
            "rbe.series_summary",
            series,
            SERIES_SUMMARY_COLS,
            truncate=False,
            force_not_null=("tipo_festivo", "fecha_especial"),
        )
        bulk_replace_table(conn, "rbe.alerts_prioritized", alerts, ALERTS_INSERT_COLS, truncate=False)
        with conn.cursor() as cur:
            cur.execute("SELECT total_alertas_dia, total_episodios, impacto_bruto_total_eur FROM rbe.v_kpi_global;")
            print("PostgreSQL v_kpi_global:", cur.fetchone())
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
