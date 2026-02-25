import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler

# Shared console instance so all packages write to the same output stream.
console = Console()

_configured = False


def setup_logging(logs_dir: Path, app_name: str = "scraper") -> None:
    """
    Configure the root parseltongue logger. Safe to call multiple times — only
    adds handlers once.

    - Console: RichHandler (INFO+) with colours and level highlighting.
    - File: plain RotatingFileHandler at logs_dir/<app_name>.log (DEBUG+, 5 MB × 3).
    """
    global _configured
    if _configured:
        return
    _configured = True

    logs_dir.mkdir(parents=True, exist_ok=True)

    file_handler = RotatingFileHandler(
        logs_dir / f"{app_name}.log",
        maxBytes=5 * 1024 * 1024,  # 5 MB per file
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s  %(levelname)-8s  %(name)s  %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    )

    console_handler = RichHandler(
        console=console,
        level=logging.INFO,
        show_path=False,
        rich_tracebacks=True,
        markup=True,
    )

    root_logger = logging.getLogger("parseltongue")
    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)


def get_logger(name: str) -> logging.Logger:
    """
    Return a logger scoped under the parseltongue namespace.
    e.g. get_logger("scraper") → logging.getLogger("parseltongue.scraper")
    """
    return logging.getLogger(f"parseltongue.{name}")
