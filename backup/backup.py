"""Daily PostgreSQL backup service.

Runs forever. Wakes up at 03:00 UTC every day, dumps the DB, gzips it,
sends the file to a Telegram chat, then sleeps until the next 03:00 UTC.
Deletes local backups older than 30 days.

Retry strategy: up to MAX_SEND_ATTEMPTS attempts with exponential backoff
(30s → 60s → 120s → 300s → 300s…). Total retry window ~30 minutes — enough
to survive Docker Desktop / WSL2 network-reset after host machine wakes from sleep.

Startup catch-up: if the last backup is older than 25 hours, run one immediately
so a missed 03:00 UTC backup (machine was asleep) is recovered when docker starts.
"""
import gzip
import logging
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from datetime import datetime, timedelta, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [backup] %(levelname)s %(message)s",
)
logger = logging.getLogger("backup")

BACKUP_DIR = "/backups"
MAX_SEND_ATTEMPTS = 12          # retries over ~30 minutes
SEND_HARD_TIMEOUT = 90          # seconds — wall-clock limit per attempt (fixes urllib hang)
PGDUMP_TIMEOUT = 120            # seconds — pg_dump subprocess hard limit
KEEP_DAYS = 30


def seconds_until_3am_utc() -> float:
    now = datetime.now(timezone.utc)
    target = now.replace(hour=3, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def _backoff(attempt: int) -> float:
    """Exponential backoff capped at 5 minutes: 30 60 120 300 300 …"""
    return min(30 * (2 ** (attempt - 1)), 300)


def _send_telegram_document(bot_token: str, chat_id: str, filepath: str,
                             caption: str) -> bool:
    """Upload file to Telegram using curl subprocess with hard wall-clock timeout.

    curl --max-time enforces a total transfer time limit regardless of TCP state,
    fixing the urllib hang-on-recv bug seen when Docker Desktop resets WSL2 network.
    Returns True on success.
    """
    filename = os.path.basename(filepath)
    cmd = [
        "curl", "--silent", "--show-error",
        "--max-time", str(SEND_HARD_TIMEOUT),
        "--retry", "0",          # we handle retries ourselves
        "-F", f"chat_id={chat_id}",
        "-F", f"caption={caption}",
        "-F", f"document=@{filepath};filename={filename}",
        f"https://api.telegram.org/bot{bot_token}/sendDocument",
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=SEND_HARD_TIMEOUT + 10)
    if result.returncode == 0:
        out = result.stdout.decode(errors="replace")
        if '"ok":true' in out:
            logger.info("Telegram OK: %s", out[:120])
            return True
        logger.warning("Telegram returned error: %s", out[:200])
    else:
        logger.warning("curl exit %d: %s", result.returncode,
                       result.stderr.decode(errors="replace")[:200])
    return False


def _send_telegram_text(bot_token: str, chat_id: str, text: str) -> None:
    """Send a plain text message. Best-effort, no retries."""
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as _:
            pass
    except Exception as e:
        logger.error("Admin alert failed: %s", e)


def run_backup() -> None:
    bot_token = os.environ.get("BOT_TOKEN", "")
    chat_id = os.environ.get("BACKUP_CHAT_ID", "")
    pg_user = os.environ.get("POSTGRES_USER", "")
    pg_pass = os.environ.get("POSTGRES_PASSWORD", "")
    pg_db = os.environ.get("POSTGRES_DB", "")
    admin_chat_id = os.environ.get("ADMIN_TELEGRAM_ID", "")

    if not all([bot_token, chat_id, pg_user, pg_pass, pg_db]):
        logger.error("Missing required env vars (BOT_TOKEN, BACKUP_CHAT_ID, POSTGRES_*)")
        return

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"backup_{timestamp}.sql.gz"
    filepath = os.path.join(BACKUP_DIR, filename)

    logger.info("Starting backup → %s", filename)

    # ── pg_dump with hard timeout ─────────────────────────────────────────────
    env = {**os.environ, "PGPASSWORD": pg_pass}
    try:
        proc = subprocess.run(
            ["pg_dump", "-h", "db", "-U", pg_user, pg_db],
            capture_output=True,
            env=env,
            timeout=PGDUMP_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        logger.error("pg_dump timed out after %d seconds — DB may be overloaded", PGDUMP_TIMEOUT)
        return

    if proc.returncode != 0:
        logger.error("pg_dump failed:\n%s", proc.stderr.decode(errors="replace"))
        return

    with gzip.open(filepath, "wb") as f:
        f.write(proc.stdout)

    size_kb = os.path.getsize(filepath) // 1024
    logger.info("Created: %s (%d KB)", filename, size_kb)

    # ── Send to Telegram with exponential-backoff retries ─────────────────────
    caption = (
        f"🗄 Бэкап БД: {datetime.now(timezone.utc).strftime('%d.%m.%Y %H:%M')} UTC\n"
        f"Файл: {filename}\nРазмер: {size_kb} KB"
    )
    sent = False
    for attempt in range(1, MAX_SEND_ATTEMPTS + 1):
        logger.info("Telegram send attempt %d/%d …", attempt, MAX_SEND_ATTEMPTS)
        try:
            ok = _send_telegram_document(bot_token, chat_id, filepath, caption)
        except Exception as exc:
            logger.warning("Attempt %d unexpected error: %s", attempt, exc)
            ok = False

        if ok:
            sent = True
            break

        if attempt < MAX_SEND_ATTEMPTS:
            wait = _backoff(attempt)
            logger.warning("Attempt %d failed — retrying in %.0f s", attempt, wait)
            time.sleep(wait)

    if not sent:
        logger.error("All %d attempts failed for %s — backup kept locally at %s",
                     MAX_SEND_ATTEMPTS, filename, filepath)
        if admin_chat_id:
            _send_telegram_text(
                bot_token, admin_chat_id,
                f"⚠️ Бэкап создан, но доставка в Telegram не удалась после "
                f"{MAX_SEND_ATTEMPTS} попыток: {filename}\n"
                f"Файл сохранён локально в контейнере.",
            )

    # ── Cleanup: keep only last KEEP_DAYS days ────────────────────────────────
    cutoff = time.time() - KEEP_DAYS * 86400
    for fname in os.listdir(BACKUP_DIR):
        fpath = os.path.join(BACKUP_DIR, fname)
        if fname.startswith("backup_") and fname.endswith(".sql.gz"):
            if os.path.isfile(fpath) and os.path.getmtime(fpath) < cutoff:
                os.remove(fpath)
                logger.info("Deleted old backup: %s", fname)

    logger.info("Backup run complete")


def _last_backup_age_hours() -> float:
    """Return hours since the most recent backup file was created. 999 if none."""
    try:
        files = [
            f for f in os.listdir(BACKUP_DIR)
            if f.startswith("backup_") and f.endswith(".sql.gz")
        ]
        if not files:
            return 999.0
        newest_mtime = max(os.path.getmtime(os.path.join(BACKUP_DIR, f)) for f in files)
        return (time.time() - newest_mtime) / 3600
    except Exception:
        return 999.0


if __name__ == "__main__":
    # --now flag: run backup immediately (manual trigger / CI smoke test)
    force_now = "--now" in sys.argv

    logger.info("Backup service started — daily at 03:00 UTC, keeping %d days locally", KEEP_DAYS)

    # Startup catch-up: if last backup is >25 h old, run one immediately.
    # Covers the case where the machine was asleep at 03:00 UTC and missed the window.
    age = _last_backup_age_hours()
    if force_now or age > 25:
        if force_now:
            logger.info("--now flag: running backup immediately")
        else:
            logger.info("Last backup is %.1f h old (>25 h) — running catch-up backup now", age)
        run_backup()

    if force_now:
        sys.exit(0)

    while True:
        wait = seconds_until_3am_utc()
        logger.info("Next scheduled backup in %.0f s (%.1f h)", wait, wait / 3600)
        time.sleep(wait)
        run_backup()
