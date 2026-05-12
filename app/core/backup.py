from __future__ import annotations

import asyncio
import os
from datetime import datetime
from typing import AsyncIterator, List, Optional, Tuple

from sqlalchemy.engine.url import make_url


def build_pg_url(database_url: str) -> str:
    url = make_url(database_url)
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


def build_backup_filename(prefix: str = "floussy_backup") -> str:
    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    return f"{prefix}_{stamp}.dump"


async def stream_pg_dump(pg_url: str) -> AsyncIterator[bytes]:
    process = await asyncio.create_subprocess_exec(
        "pg_dump",
        "--format=custom",
        "--no-owner",
        "--no-privileges",
        "--dbname",
        pg_url,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert process.stdout is not None
    assert process.stderr is not None

    try:
        while True:
            chunk = await process.stdout.read(1024 * 1024)
            if not chunk:
                break
            yield chunk
        stderr = await process.stderr.read()
        returncode = await process.wait()
        if returncode != 0:
            message = stderr.decode("utf-8", errors="ignore").strip()
            raise RuntimeError(message or "pg_dump failed")
    finally:
        if process.returncode is None:
            process.kill()


async def run_pg_dump_to_file(pg_url: str, dest_path: str) -> Tuple[bool, str, int]:
    process = await asyncio.create_subprocess_exec(
        "pg_dump",
        "--format=custom",
        "--no-owner",
        "--no-privileges",
        "--file",
        dest_path,
        "--dbname",
        pg_url,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        message = stderr.decode("utf-8", errors="ignore").strip()
        if stdout:
            message = f"{message}\n{stdout.decode('utf-8', errors='ignore').strip()}"
        return False, message or "pg_dump failed", 0
    size = 0
    try:
        size = os.path.getsize(dest_path)
    except OSError:
        size = 0
    return True, "", size


async def run_pg_restore(pg_url: str, dump_path: str, mode: str) -> Tuple[bool, str]:
    args = [
        "pg_restore",
        "--no-owner",
        "--no-privileges",
        "--dbname",
        pg_url,
    ]
    if mode == "replace":
        args.extend(["--clean", "--if-exists"])
    process = await asyncio.create_subprocess_exec(
        *args,
        dump_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        message = stderr.decode("utf-8", errors="ignore").strip()
        if stdout:
            message = f"{message}\n{stdout.decode('utf-8', errors='ignore').strip()}"
        lowered = message.lower()
        if "unrecognized configuration parameter \"transaction_timeout\"" in lowered:
            return True, (
                "Avertissement: paramètre transaction_timeout non supporté, "
                "restauration poursuivie."
            )
        return False, message or "pg_restore failed"
    return True, ""


async def terminate_db_connections(
    pg_url: str,
    exclude_pid: Optional[int] = None,
) -> Tuple[bool, str]:
    query = (
        "SELECT pg_terminate_backend(pid) "
        "FROM pg_stat_activity "
        "WHERE datname = current_database() "
        "AND pid <> pg_backend_pid()"
    )
    if exclude_pid:
        query = f"{query} AND pid <> {exclude_pid}"
    process = await asyncio.create_subprocess_exec(
        "psql",
        pg_url,
        "-c",
        query,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        message = stderr.decode("utf-8", errors="ignore").strip()
        if stdout:
            message = f"{message}\n{stdout.decode('utf-8', errors='ignore').strip()}"
        return False, message or "psql terminate failed"
    return True, ""


async def validate_dump_file(dump_path: str) -> Tuple[bool, str]:
    process = await asyncio.create_subprocess_exec(
        "pg_restore",
        "--list",
        dump_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        message = stderr.decode("utf-8", errors="ignore").strip()
        if stdout:
            message = f"{message}\n{stdout.decode('utf-8', errors='ignore').strip()}"
        return False, message or "Dump validation failed"
    return True, ""


def ensure_backup_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def list_backup_files(path: str, prefix: str) -> List[str]:
    if not os.path.isdir(path):
        return []
    files = [
        os.path.join(path, name)
        for name in os.listdir(path)
        if name.startswith(prefix) and name.endswith(".dump")
    ]
    return sorted(files, reverse=True)


def cleanup_old_backups(path: str, prefix: str, keep: int = 1) -> List[str]:
    files = list_backup_files(path, prefix)
    removed: List[str] = []
    for old in files[keep:]:
        try:
            os.remove(old)
            removed.append(old)
        except OSError:
            continue
    return removed
