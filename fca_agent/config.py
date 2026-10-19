import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader so the bot runs without extra packages."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


@dataclass(frozen=True)
class Config:
    bot_token: str
    admin_ids: frozenset[int]
    db_path: Path
    backup_dir: Path


def load_config(env_file: str = ".env") -> Config:
    _load_dotenv(Path(env_file))
    token = os.environ.get("BOT_TOKEN", "").strip()
    admins = os.environ.get("ADMIN_IDS", "").strip()
    if not token:
        raise SystemExit("BOT_TOKEN is missing. Copy .env.example to .env and fill it in.")
    return Config(
        bot_token=token,
        admin_ids=frozenset(int(x) for x in admins.split(",") if x.strip()),
        db_path=Path(os.environ.get("DB_PATH", "data/agent.db")),
        backup_dir=Path(os.environ.get("BACKUP_DIR", "data/backups")),
    )
