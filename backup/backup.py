"""Daily PostgreSQL backup service.

Runs forever. Wakes up at 03:00 UTC every day, dumps the DB, gzips it,
sends the file to a Telegram chat, then sleeps until the next 03:00 UTC.
Deletes local backups older than 30 days.
"""
import gzip
import logging
import os
import subprocess
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [backup] %(levelname)s %(message)s",
)
logger = logging.getLogger("backup")


def seconds_until_3am_utc() -> float:
    now = datetime.now(timezone.utc)
    target = now.replace(hour=3, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def run_backup() -> None:
    bot_token = os.environ.get("BOT_TOKEN", "")
    chat_id = os.environ.get("BACKUP_CHAT_ID", "")
    pg_user = os.environ.get("POSTGRES_USER", "")
    pg_pass = os.environ.get("POSTGRES_PASSWORD", "")
    pg_db = os.environ.get("POSTGRES_DB", "")

    if not all([bot_token, chat_id, pg_user, pg_pass, pg_db]):
        logger.error("Missing required env vars (BOT_TOKEN, BACKUP_CHAT_ID, POSTGRES_*)")
        return

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"backup_{timestamp}.sql.gz"
    filepath = f"/backups/{filename}"

    logger.info("Starting backup → %s", filename)

    env = {**os.environ, "PGPASSWORD": pg_pass}
    proc = subprocess.run(
        ["pg_dump", "-h", "db", "-U", pg_user, pg_db],
        capture_output=True,
        env=env,
    )
    if proc.returncode != 0:
        logger.error("pg_dump failed:\n%s", proc.stderr.decode())
        return

    with gzip.open(filepath, "wb") as f:
        f.write(proc.stdout)

    size_kb = os.path.getsize(filepath) // 1024
    logger.info("Created: %s (%d KB)", filename, size_kb)

    # ── Send to Telegram ──────────────────────────────────────────────────────
    caption = (
        f"🗄 Бэкап БД: {datetime.now(timezone.utc).strftime('%d.%m.%Y %H:%M')} UTC\n"
        f"Файл: {filename}\nРазмер: {size_kb} KB"
    )
    boundary = "backupboundary42"
    with open(filepath, "rb") as f:
        file_bytes = f.read()

    body = (
        f"--{boundary}\r\n"
        f"Content-Disposition: form-data; name=\"chat_id\"\r\n\r\n"
        f"{chat_id}\r\n"
        f"--{boundary}\r\n"
        f"Content-Disposition: form-data; name=\"caption\"\r\n\r\n"
        f"{caption}\r\n"
        f"--{boundary}\r\n"
        f"Content-Disposition: form-data; name=\"document\"; filename=\"{filename}\"\r\n"
        f"Content-Type: application/octet-stream\r\n\r\n"
    ).encode("utf-8") + file_bytes + f"\r\n--{boundary}--\r\n".encode("utf-8")

    url = f"https://api.telegram.org/bot{bot_token}/sendDocument"
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    sent = False
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = resp.read().decode()
                logger.info("Telegram OK: %s", result[:120])
            sent = True
            break
        except Exception as exc:
            logger.warning("Telegram send attempt %d/3 failed: %s", attempt, exc)
            if attempt < 3:
                time.sleep(30)

    if not sent:
        logger.error("All 3 Telegram send attempts failed for backup %s", filename)
        # Send plain text alert to admin via sendMessage
        admin_token = bot_token
        admin_chat_id = os.environ.get("ADMIN_TELEGRAM_ID", "")
        if admin_chat_id:
            try:
                alert_data = urllib.parse.urlencode({
                    "chat_id": admin_chat_id,
                    "text": f"⚠️ Бэкап создан, но отправка в Telegram не удалась: {filename}",
                }).encode("utf-8")
                alert_req = urllib.request.Request(
                    f"https://api.telegram.org/bot{admin_token}/sendMessage",
                    data=alert_data,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                urllib.request.urlopen(alert_req, timeout=15)
            except Exception as alert_exc:
                logger.error("Failed to send admin alert: %s", alert_exc)

    # ── Cleanup: keep only last 30 days ───────────────────────────────────────
    cutoff = time.time() - 30 * 86400
    for fname in os.listdir("/backups"):
        fpath = os.path.join("/backups", fname)
        if fname.startswith("backup_") and fname.endswith(".sql.gz"):
            if os.path.isfile(fpath) and os.path.getmtime(fpath) < cutoff:
                os.remove(fpath)
                logger.info("Deleted old backup: %s", fname)

    logger.info("Backup complete")


if __name__ == "__main__":
    logger.info("Backup service started — daily at 03:00 UTC, keeping 30 days locally")
    while True:
        wait = seconds_until_3am_utc()
        logger.info("Next backup in %.0f s (%.1f h)", wait, wait / 3600)
        time.sleep(wait)
        run_backup()
