"""Ingestion run bookkeeping — makes long loads observable and resumable (§32)."""
from __future__ import annotations

from app.db import sync_conn


def start_run(
    source_key: str, unit: str, file_bytes: int | None = None,
    file_signature: str | None = None,
) -> int:
    with sync_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ingestion_runs
                    (source_key, unit, status, file_bytes, file_signature, started_at)
                VALUES (%s, %s, 'running', %s, %s, now())
                ON CONFLICT (source_key, unit) DO UPDATE SET
                    status = 'running', started_at = now(), finished_at = NULL,
                    error = NULL, rows_read = 0, rows_written = 0,
                    rows_rejected = 0, file_bytes = EXCLUDED.file_bytes,
                    file_signature = EXCLUDED.file_signature
                RETURNING id
                """,
                (source_key, unit, file_bytes, file_signature),
            )
            run_id = cur.fetchone()["id"]
        conn.commit()
    return run_id


def finish_run(
    run_id: int, status: str, rows_read: int, rows_written: int,
    rows_rejected: int, error: str | None = None,
) -> None:
    with sync_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE ingestion_runs SET status=%s, rows_read=%s, rows_written=%s,
                       rows_rejected=%s, error=%s, finished_at=now()
                WHERE id=%s
                """,
                (status, rows_read, rows_written, rows_rejected,
                 (error or "")[:2000] or None, run_id),
            )
        conn.commit()


def is_complete(source_key: str, unit: str, file_signature: str | None = None) -> bool:
    """True if this unit already loaded successfully from the same file version.

    Lets `ingest all` be re-run after an interruption without redoing work.
    """
    with sync_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT status, file_signature FROM ingestion_runs "
            "WHERE source_key=%s AND unit=%s",
            (source_key, unit),
        )
        row = cur.fetchone()
    if not row or row["status"] != "complete":
        return False
    # A changed file signature means the source file was re-downloaded, so the
    # unit must be re-ingested even though a previous run succeeded.
    return not (file_signature is not None and row["file_signature"] != file_signature)
