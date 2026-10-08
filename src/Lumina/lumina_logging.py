"""Shared logging setup: file logging that never locks the log file.

Windows locks open files, so a permanently-open ``FileHandler`` breaks
Krita's plugin importer: reinstalling deletes the old plugin directory and
``shutil.rmtree`` raises ``PermissionError: [WinError 32]`` on the held
``lumina_log.txt``. The handler here opens/appends/closes on every record,
so the file is never locked between writes. Slightly more per-write work,
but log volume is tens of lines per second at most.
"""
import logging
import os

_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "lumina_log.txt")
_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


class ReopenFileHandler(logging.Handler):
    """Append one record at a time; the file is never held open."""

    def __init__(self, path):
        super().__init__()
        self.baseFilename = os.path.abspath(path)

    def emit(self, record):
        try:
            with open(self.baseFilename, "a", encoding="utf-8") as f:
                f.write(self.format(record) + "\n")
        except Exception:  # pragma: no cover - disk/IO only
            self.handleError(record)


def get_logger():
    """Return the ``Lumina`` logger, attaching the file handler once."""
    LOG = logging.getLogger("Lumina")
    LOG.setLevel(logging.DEBUG)
    want = os.path.abspath(_LOG_PATH)
    for h in LOG.handlers:
        if isinstance(h, ReopenFileHandler) and \
                os.path.abspath(getattr(h, "baseFilename", "") or "") == want:
            break
    else:
        handler = ReopenFileHandler(_LOG_PATH)
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(logging.Formatter(_FORMAT))
        LOG.addHandler(handler)
    return LOG


try:
    LOG = get_logger()
except Exception as exc:  # pragma: no cover - logging must never break the plugin
    print(f"Lumina: logging setup failed - {exc}")
    LOG = logging.getLogger("Lumina")
