from __future__ import annotations

import asyncio
import os
import tempfile
import time

from typing import Optional
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi.responses import StreamingResponse

from app.api.deps import get_current_user
from app.core.admin_activity import create_admin_log
from app.core.backup import (
    build_backup_filename,
    build_pg_url,
    cleanup_old_backups,
    ensure_backup_dir,
    run_pg_dump_to_file,
    run_pg_restore,
    stream_pg_dump,
    terminate_db_connections,
    validate_dump_file,
)
from app.core.backup_state import add_backup_log, is_backup_in_progress, set_backup_in_progress
from app.core.config import get_settings
from app.core.rate_limit import get_client_ip
from app.db.session import get_db, get_sessionmaker, reset_engine
from app.models import User
from app.models.backup_record import BackupRecord
from app.schemas.backup_record import BackupRecordOut, BackupStatusOut

router = APIRouter(prefix="/admin/backups")


def _require_superadmin(user: User) -> None:
    if user.role != "superadmin":
        raise HTTPException(status_code=403, detail="Forbidden")


async def _safe_db_log(
    event_type: str,
    message: str,
    status: str = "info",
    actor_email: Optional[str] = None,
    actor_ip: Optional[str] = None,
) -> None:
    try:
        SessionLocal = get_sessionmaker()
        async with SessionLocal() as db:
            await create_admin_log(
                db,
                event_type=event_type,
                message=message,
                status=status,
                actor_email=actor_email,
                actor_ip=actor_ip,
            )
    except Exception:
        return


async def _safe_backup_record(
    *,
    kind: str,
    status: str,
    actor_email: str | None = None,
    actor_ip: str | None = None,
    mode: str | None = None,
    file_name: str | None = None,
    file_size_bytes: int | None = None,
    duration_ms: int | None = None,
    message: str | None = None,
    created_at: datetime | None = None,
    completed_at: datetime | None = None,
) -> None:
    try:
        SessionLocal = get_sessionmaker()
        async with SessionLocal() as db:
            record = BackupRecord(
                created_at=created_at or datetime.now(timezone.utc),
                completed_at=completed_at,
                kind=kind,
                status=status,
                mode=mode,
                file_name=file_name,
                file_size_bytes=file_size_bytes,
                duration_ms=duration_ms,
                actor_email=actor_email,
                actor_ip=actor_ip,
                message=message,
            )
            db.add(record)
            await db.commit()
    except Exception:
        return


@router.post("/export")
async def export_backup(
    request: Request,
    current_user: User = Depends(get_current_user),
) -> StreamingResponse:
    _require_superadmin(current_user)
    actor_ip = get_client_ip(request)
    actor_email = current_user.email
    settings = get_settings()
    pg_url = build_pg_url(settings.database_url)
    filename = build_backup_filename()
    started_at = datetime.now(timezone.utc)
    started_ts = time.monotonic()
    bytes_sent = 0
    add_backup_log(
        "backup_export_started",
        "Export complet démarré.",
        actor_email=actor_email,
        actor_ip=actor_ip,
    )
    await _safe_db_log(
        "backup_export_started",
        "Export complet démarré.",
        actor_email=actor_email,
        actor_ip=actor_ip,
    )

    async def generator():
        nonlocal bytes_sent
        try:
            async for chunk in stream_pg_dump(pg_url):
                bytes_sent += len(chunk)
                yield chunk
        except Exception as exc:
            add_backup_log(
                "backup_export_failed",
                f"Export échoué: {exc}",
                "error",
                actor_email=actor_email,
                actor_ip=actor_ip,
            )
            await _safe_db_log(
                "backup_export_failed",
                f"Export échoué: {exc}",
                "error",
                actor_email=actor_email,
                actor_ip=actor_ip,
            )
            duration_ms = int((time.monotonic() - started_ts) * 1000)
            await _safe_backup_record(
                kind="export",
                status="error",
                actor_email=actor_email,
                actor_ip=actor_ip,
                file_name=filename,
                file_size_bytes=bytes_sent,
                duration_ms=duration_ms,
                message=str(exc),
                created_at=started_at,
                completed_at=datetime.now(timezone.utc),
            )
            raise
        else:
            add_backup_log(
                "backup_export_completed",
                "Export terminé avec succès.",
                "success",
                actor_email=actor_email,
                actor_ip=actor_ip,
            )
            await _safe_db_log(
                "backup_export_completed",
                "Export terminé avec succès.",
                "success",
                actor_email=actor_email,
                actor_ip=actor_ip,
            )
            duration_ms = int((time.monotonic() - started_ts) * 1000)
            await _safe_backup_record(
                kind="export",
                status="success",
                actor_email=actor_email,
                actor_ip=actor_ip,
                file_name=filename,
                file_size_bytes=bytes_sent,
                duration_ms=duration_ms,
                created_at=started_at,
                completed_at=datetime.now(timezone.utc),
            )
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return StreamingResponse(
        generator(),
        media_type="application/octet-stream",
        headers=headers,
    )


@router.post("/import")
async def import_backup(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    mode: str = Form("replace"),
    file: UploadFile = File(...),
) -> dict:
    _require_superadmin(current_user)
    if mode not in {"replace", "merge"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid import mode",
        )
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing file")

    actor_ip = get_client_ip(request)
    actor_email = current_user.email
    settings = get_settings()
    pg_url = build_pg_url(settings.database_url)
    set_backup_in_progress(True)
    add_backup_log(
        "backup_import_started",
        "Import démarré (upload du fichier).",
        actor_email=actor_email,
        actor_ip=actor_ip,
    )
    tmp_path = None
    started_at = datetime.now(timezone.utc)
    started_ts = time.monotonic()
    file_size = 0
    success = False
    try:
        if mode == "replace":
            add_backup_log(
                "backup_import_prepare",
                "Préparation restauration: fermeture des connexions actives.",
                actor_email=actor_email,
                actor_ip=actor_ip,
            )
            try:
                await db.close()
            except Exception:
                pass
            ok, message = await terminate_db_connections(pg_url)
            if ok:
                add_backup_log(
                    "backup_import_prepare_done",
                    "Connexions actives terminées.",
                    "success",
                    actor_email=actor_email,
                    actor_ip=actor_ip,
                )
            else:
                add_backup_log(
                    "backup_import_prepare_failed",
                    f"Impossible de fermer toutes les connexions: {message}",
                    "warning",
                    actor_email=actor_email,
                    actor_ip=actor_ip,
                )
        with tempfile.NamedTemporaryFile(delete=False, suffix=".dump") as tmp:
            tmp_path = tmp.name
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                tmp.write(chunk)
        file_size = os.path.getsize(tmp_path) if tmp_path else 0
        add_backup_log(
            "backup_import_validate",
            "Vérification de l’intégrité du dump...",
            actor_email=actor_email,
            actor_ip=actor_ip,
        )
        ok, message = await validate_dump_file(tmp_path)
        if not ok:
            add_backup_log(
                "backup_import_failed",
                f"Dump invalide: {message}",
                "error",
                actor_email=actor_email,
                actor_ip=actor_ip,
            )
            await _safe_db_log(
                "backup_import_failed",
                f"Dump invalide: {message}",
                "error",
                actor_email=actor_email,
                actor_ip=actor_ip,
            )
            await _safe_backup_record(
                kind="import",
                status="error",
                actor_email=actor_email,
                actor_ip=actor_ip,
                mode=mode,
                file_name=file.filename,
                file_size_bytes=file_size,
                duration_ms=int((time.monotonic() - started_ts) * 1000),
                message=f"Dump invalide: {message}",
                created_at=started_at,
                completed_at=datetime.now(timezone.utc),
            )
            raise HTTPException(
                status_code=400,
                detail=message,
            )
        if mode == "replace":
            backup_dir = settings.backup_storage_dir
            ensure_backup_dir(backup_dir)
            snapshot_name = build_backup_filename(prefix="snapshot_before_import")
            snapshot_path = os.path.join(backup_dir, snapshot_name)
            add_backup_log(
                "backup_snapshot_started",
                "Snapshot avant import...",
                actor_email=actor_email,
                actor_ip=actor_ip,
            )
            ok, message, snap_size = await run_pg_dump_to_file(pg_url, snapshot_path)
            if not ok:
                add_backup_log(
                    "backup_snapshot_failed",
                    f"Snapshot échoué: {message}",
                    "error",
                    actor_email=actor_email,
                    actor_ip=actor_ip,
                )
                await _safe_db_log(
                    "backup_snapshot_failed",
                    f"Snapshot échoué: {message}",
                    "error",
                    actor_email=actor_email,
                    actor_ip=actor_ip,
                )
                await _safe_backup_record(
                    kind="snapshot",
                    status="error",
                    actor_email=actor_email,
                    actor_ip=actor_ip,
                    file_name=snapshot_name,
                    file_size_bytes=snap_size,
                    duration_ms=int((time.monotonic() - started_ts) * 1000),
                    message=message,
                    created_at=started_at,
                    completed_at=datetime.now(timezone.utc),
                )
                raise HTTPException(
                    status_code=500,
                    detail=message,
                )
            cleanup_old_backups(
                backup_dir, prefix="snapshot_before_import", keep=1
            )
            await _safe_backup_record(
                kind="snapshot",
                status="success",
                actor_email=actor_email,
                actor_ip=actor_ip,
                file_name=snapshot_name,
                file_size_bytes=snap_size,
                duration_ms=int((time.monotonic() - started_ts) * 1000),
                created_at=started_at,
                completed_at=datetime.now(timezone.utc),
            )
            add_backup_log(
                "backup_snapshot_completed",
                "Snapshot terminé.",
                "success",
                actor_email=actor_email,
                actor_ip=actor_ip,
            )
        add_backup_log(
            "backup_import_restore",
            "Restauration en cours...",
            actor_email=actor_email,
            actor_ip=actor_ip,
        )
        ok, message = await run_pg_restore(pg_url, tmp_path, mode)
        if not ok:
            add_backup_log(
                "backup_import_failed",
                f"Restauration échouée: {message}",
                "error",
                actor_email=actor_email,
                actor_ip=actor_ip,
            )
            await _safe_db_log(
                "backup_import_failed",
                f"Restauration échouée: {message}",
                "error",
                actor_email=actor_email,
                actor_ip=actor_ip,
            )
            await _safe_backup_record(
                kind="import",
                status="error",
                actor_email=actor_email,
                actor_ip=actor_ip,
                mode=mode,
                file_name=file.filename,
                file_size_bytes=file_size,
                duration_ms=int((time.monotonic() - started_ts) * 1000),
                message=message,
                created_at=started_at,
                completed_at=datetime.now(timezone.utc),
            )
            raise HTTPException(
                status_code=500,
                detail=message,
            )
        if message:
            add_backup_log(
                "backup_import_warning",
                message,
                "warning",
                actor_email=actor_email,
                actor_ip=actor_ip,
            )
            await _safe_db_log(
                "backup_import_warning",
                message,
                "warning",
                actor_email=actor_email,
                actor_ip=actor_ip,
            )
        process = await asyncio.create_subprocess_exec(
            "alembic",
            "upgrade",
            "head",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            error_message = stderr.decode("utf-8", errors="ignore").strip()
            if stdout:
                error_message = (
                    f"{error_message}\n{stdout.decode('utf-8', errors='ignore').strip()}"
                )
            add_backup_log(
                "backup_import_failed",
                f"Migration échouée: {error_message}",
                "error",
                actor_email=actor_email,
                actor_ip=actor_ip,
            )
            await _safe_db_log(
                "backup_import_failed",
                f"Migration échouée: {error_message}",
                "error",
                actor_email=actor_email,
                actor_ip=actor_ip,
            )
            await _safe_backup_record(
                kind="import",
                status="error",
                actor_email=actor_email,
                actor_ip=actor_ip,
                mode=mode,
                file_name=file.filename,
                file_size_bytes=file_size,
                duration_ms=int((time.monotonic() - started_ts) * 1000),
                message=error_message,
                created_at=started_at,
                completed_at=datetime.now(timezone.utc),
            )
            raise HTTPException(
                status_code=500,
                detail=error_message or "Migration failed",
            )
        add_backup_log(
            "backup_import_migrated",
            "Migrations appliquées après restauration.",
            "success",
            actor_email=actor_email,
            actor_ip=actor_ip,
        )
        await _safe_db_log(
            "backup_import_migrated",
            "Migrations appliquées après restauration.",
            "success",
            actor_email=actor_email,
            actor_ip=actor_ip,
        )
        add_backup_log(
            "backup_import_completed",
            "Import terminé avec succès.",
            "success",
            actor_email=actor_email,
            actor_ip=actor_ip,
        )
        await _safe_db_log(
            "backup_import_completed",
            "Import terminé avec succès.",
            "success",
            actor_email=actor_email,
            actor_ip=actor_ip,
        )
        await _safe_backup_record(
            kind="import",
            status="success",
            actor_email=actor_email,
            actor_ip=actor_ip,
            mode=mode,
            file_name=file.filename,
            file_size_bytes=file_size,
            duration_ms=int((time.monotonic() - started_ts) * 1000),
            created_at=started_at,
            completed_at=datetime.now(timezone.utc),
        )
        await reset_engine()
        add_backup_log(
            "backup_import_reconnect",
            "Connexion base réinitialisée côté API.",
            "success",
            actor_email=actor_email,
            actor_ip=actor_ip,
        )
        success = True
        return {"status": "ok"}
    finally:
        await file.close()
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)
        set_backup_in_progress(False)
        if not success:
            add_backup_log(
                "backup_import_failed",
                "Import interrompu. Consulte les logs pour le détail.",
                "error",
                actor_email=actor_email,
                actor_ip=actor_ip,
            )


@router.get("/history", response_model=list[BackupRecordOut])
async def get_backup_history(
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[BackupRecordOut]:
    _require_superadmin(current_user)
    limit = max(1, min(limit, 200))
    result = await db.execute(
        select(BackupRecord).order_by(desc(BackupRecord.created_at)).limit(limit)
    )
    return [BackupRecordOut.model_validate(row) for row in result.scalars().all()]


@router.get("/status", response_model=BackupStatusOut)
async def get_backup_status(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> BackupStatusOut:
    _require_superadmin(current_user)
    settings = get_settings()
    scheduled = await db.execute(
        select(BackupRecord)
        .where(BackupRecord.kind == "scheduled", BackupRecord.status == "success")
        .order_by(desc(BackupRecord.created_at))
        .limit(1)
    )
    snapshot = await db.execute(
        select(BackupRecord)
        .where(BackupRecord.kind == "snapshot", BackupRecord.status == "success")
        .order_by(desc(BackupRecord.created_at))
        .limit(1)
    )
    last_scheduled = scheduled.scalars().first()
    last_snapshot = snapshot.scalars().first()
    return BackupStatusOut(
        last_scheduled=(
            BackupRecordOut.model_validate(last_scheduled)
            if last_scheduled
            else None
        ),
        last_snapshot=(
            BackupRecordOut.model_validate(last_snapshot)
            if last_snapshot
            else None
        ),
        retention_count=settings.backup_retention_count,
        schedule_days=settings.backup_schedule_days,
    )


@router.post("/scheduled")
async def run_scheduled_backup(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    settings = get_settings()
    token = settings.backup_cron_token
    if token:
        header = request.headers.get("x-backup-token")
        if header != token:
            raise HTTPException(status_code=403, detail="Forbidden")
    else:
        host = request.client.host if request.client else ""
        if host not in {"127.0.0.1", "::1", "localhost"}:
            raise HTTPException(status_code=403, detail="Forbidden")
    if is_backup_in_progress():
        return {"status": "skipped", "reason": "backup_in_progress"}

    pg_url = build_pg_url(settings.database_url)
    backup_dir = settings.backup_storage_dir
    ensure_backup_dir(backup_dir)

    last = await db.execute(
        select(BackupRecord)
        .where(BackupRecord.kind == "scheduled", BackupRecord.status == "success")
        .order_by(desc(BackupRecord.created_at))
        .limit(1)
    )
    last_record = last.scalars().first()
    if last_record:
        next_due = last_record.created_at + timedelta(
            days=settings.backup_schedule_days
        )
        if datetime.now(timezone.utc) < next_due:
            return {"status": "skipped", "next_due": next_due.isoformat()}

    filename = build_backup_filename(prefix="scheduled")
    dest_path = os.path.join(backup_dir, filename)
    started_at = datetime.now(timezone.utc)
    started_ts = time.monotonic()
    ok, message, size = await run_pg_dump_to_file(pg_url, dest_path)
    duration_ms = int((time.monotonic() - started_ts) * 1000)
    if not ok:
        await _safe_backup_record(
            kind="scheduled",
            status="error",
            file_name=filename,
            file_size_bytes=size,
            duration_ms=duration_ms,
            message=message,
            created_at=started_at,
            completed_at=datetime.now(timezone.utc),
        )
        raise HTTPException(status_code=500, detail=message)

    cleanup_old_backups(
        backup_dir, prefix="scheduled", keep=settings.backup_retention_count
    )
    await _safe_backup_record(
        kind="scheduled",
        status="success",
        file_name=filename,
        file_size_bytes=size,
        duration_ms=duration_ms,
        created_at=started_at,
        completed_at=datetime.now(timezone.utc),
    )
    return {"status": "ok", "file": filename, "size": size}
