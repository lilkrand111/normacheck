"""Atomic files and immutable original uploads. No database is used."""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import logging
import os
from pathlib import Path
import secrets
import shutil
import tempfile
import uuid

LOG = logging.getLogger(__name__)


def utc_now():
    return datetime.now(timezone.utc)


def atomic_json(path, data):
    path = Path(path)
    descriptor, temporary = tempfile.mkstemp(prefix=".json-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class Storage:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.documents = self.root / "documents"
        self.documents.mkdir(exist_ok=True, mode=0o700)

    @contextmanager
    def instance_lock(self):
        # One API process coordinates several concurrent child workers. A second
        # API process must not recover or clean up the first one's active jobs.
        with (self.root / ".server.lock").open("a+b") as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("Storage is already in use. Run one Uvicorn API worker; configure MAX_CONCURRENT_JOBS for parallel DOCX processing.") from exc
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)

    def local_secret(self):
        path = self.root / ".session-secret"
        if not path.exists():
            with path.open("x", encoding="ascii") as stream:
                os.chmod(path, 0o600)
                stream.write(secrets.token_urlsafe(48))
        return path.read_text(encoding="ascii").strip()

    def path(self, document_id):
        try:
            if str(uuid.UUID(document_id)) != document_id:
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            raise FileNotFoundError("Document not found") from None
        path = self.documents / document_id
        if path.is_symlink():
            raise FileNotFoundError("Document not found")
        return path

    def read(self, document_id):
        return json.loads((self.path(document_id) / "metadata.json").read_text(encoding="utf-8"))

    def write(self, metadata):
        atomic_json(self.path(metadata["id"]) / "metadata.json", metadata)

    def create(self, owner_id, filename, settings):
        from formal_check import __version__

        document_id = str(uuid.uuid4())
        directory = self.path(document_id)
        directory.mkdir(mode=0o700)
        now = utc_now()
        metadata = {
            "id": document_id, "owner_id": owner_id,
            "original_filename": filename,
            "created_at": now.isoformat(),
            "expires_at": None,
            "state": "checking", "current_version": None,
            "checker_version": __version__, "ruleset_version": settings.ruleset_version,
            "last_error": None,
        }
        self.write(metadata)
        return metadata

    def usage(self):
        return sum(p.stat().st_size for p in self.documents.rglob("*") if p.is_file() and not p.is_symlink())

    def recover(self):
        for directory in self.documents.iterdir():
            if not directory.is_dir() or directory.is_symlink():
                continue
            try:
                metadata = self.read(directory.name)
                changed = False
                if metadata.get("expires_at") is not None:
                    # Convert legacy 24-hour uploads before any cleanup runs.
                    metadata["expires_at"] = None
                    changed = True
                if metadata["state"] in ("checking", "fixing"):
                    metadata.update(state="failed", last_error={"code": "interrupted", "message": "Обработка прервана перезапуском сервера. Повторите попытку."})
                    changed = True
                if changed:
                    self.write(metadata)
                for work in directory.glob(".work-*"):
                    if work.is_dir() and not work.is_symlink():
                        shutil.rmtree(work)
            except (OSError, ValueError, KeyError):
                LOG.exception("Could not recover document %s", directory.name)

