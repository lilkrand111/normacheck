"""Coordinate parallel documents and publish only completely checked results."""
import asyncio
from contextlib import asynccontextmanager
import json
import logging
import os
from pathlib import Path
import shutil
import tempfile

from formal_check.coverage import LIMITATIONS
from formal_check.report import validate_report

from .runner import ProcessingError, Runner
from .storage import Storage

LOG = logging.getLogger(__name__)


class Service:
    def __init__(self, settings):
        self.settings = settings
        self.storage = Storage(settings.storage_dir)
        self.runner = Runner(settings)
        self.slots = asyncio.Semaphore(settings.max_concurrent_jobs)
        self.operations = set()
        self.reserved_bytes = 0

    @asynccontextmanager
    async def slot(self):
        if self.slots.locked():
            raise ProcessingError("busy", "Все обработчики заняты. Повторите попытку немного позже.", 503)
        await self.slots.acquire()
        try:
            yield
        finally:
            self.slots.release()

    def access(self, document_id, owner_id):
        try:
            metadata = self.storage.read(document_id)
        except FileNotFoundError:
            raise ProcessingError("not_found", "Документ не найден.", 404) from None
        if metadata["owner_id"] != owner_id:
            raise ProcessingError("not_found", "Документ не найден.", 404)
        return metadata

    def view(self, metadata):
        document_id = metadata["id"]
        version = metadata["current_version"]
        report, warnings = None, []
        if version:
            directory = self.storage.path(document_id)
            report = validate_report(json.loads((directory / f"{version}-report.json").read_text(encoding="utf-8")))
            warnings_path = directory / f"{version}-warnings.json"
            if warnings_path.exists():
                warnings = json.loads(warnings_path.read_text(encoding="utf-8"))
        return {
            "document_id": document_id, "filename": metadata["original_filename"],
            "state": metadata["state"], "current_version": version,
            "created_at": metadata["created_at"],
            "expires_at": None,
            "checker_version": metadata["checker_version"], "ruleset_version": metadata["ruleset_version"],
            "last_error": metadata["last_error"], "report": report,
            "warnings": warnings,
            "limitations": [{"rule_id": key, "description": value} for key, value in LIMITATIONS.items()],
            "download_url": f"/api/documents/{document_id}/download" if version == "corrected" else None,
        }

    async def upload(self, upload, owner_id):
        filename = (upload.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
        filename = "".join(c for c in filename if c.isprintable())[:200]
        if not filename.lower().endswith(".docx"):
            raise ProcessingError("unsupported_format", "Поддерживаются только файлы .docx.", 415)
        if upload.size is not None and upload.size > self.settings.max_upload_bytes:
            raise ProcessingError("file_too_large", "Файл превышает допустимый размер.", 413)
        if upload.size == 0:
            raise ProcessingError("empty_file", "Выбран пустой файл.", 422)
        reservation = self.settings.max_upload_bytes * 3 + 5 * 1024 * 1024
        if self.storage.usage() + self.reserved_bytes + reservation > self.settings.max_storage_bytes:
            raise ProcessingError("storage_full", "Хранилище заполнено. Повторите попытку позже.", 507)
        if shutil.disk_usage(self.storage.root).free < reservation + 128 * 1024 * 1024:
            raise ProcessingError("storage_full", "На сервере недостаточно свободного места.", 507)
        self.reserved_bytes += reservation
        metadata = None
        try:
            metadata = self.storage.create(owner_id, filename, self.settings)
            document_id = metadata["id"]
            self.operations.add(document_id)
            directory = self.storage.path(document_id)
            size = 0
            with (directory / ".upload").open("xb") as stream:
                while chunk := await upload.read(256 * 1024):
                    size += len(chunk)
                    if size > self.settings.max_upload_bytes:
                        raise ProcessingError("file_too_large", "Файл превышает допустимый размер.", 413)
                    stream.write(chunk)
                stream.flush()
                os.fsync(stream.fileno())
            if not size:
                raise ProcessingError("empty_file", "Выбран пустой файл.", 422)
            os.replace(directory / ".upload", directory / "original.docx")
            await self.process(metadata, "check")
            return self.view(metadata)
        except BaseException as exc:
            if metadata is not None and not (self.storage.path(metadata["id"]) / "original.docx").exists():
                # Retain already received bytes (.upload), even if the transfer
                # failed. Never delete a document directory on an error path.
                metadata.update(state="failed", last_error={
                    "code": exc.code if isinstance(exc, ProcessingError) else "upload_interrupted",
                    "message": exc.message if isinstance(exc, ProcessingError) else "Загрузка прервана. Полученная часть файла сохранена.",
                })
                try:
                    self.storage.write(metadata)
                except OSError:
                    LOG.exception("Could not record interrupted upload %s", metadata["id"])
            raise
        finally:
            self.reserved_bytes -= reservation
            if metadata is not None:
                self.operations.discard(metadata["id"])

    async def fix(self, document_id, owner_id):
        metadata = self.access(document_id, owner_id)
        if document_id in self.operations:
            raise ProcessingError("document_busy", "Документ уже обрабатывается.", 409)
        if metadata["current_version"] == "corrected":
            return self.view(metadata)
        if metadata["current_version"] != "original":
            raise ProcessingError("not_checked", "Сначала необходимо успешно проверить документ.", 409)
        async with self.slot():
            self.operations.add(document_id)
            try:
                await self.process(metadata, "fix")
                return self.view(metadata)
            finally:
                self.operations.discard(document_id)

    async def process(self, metadata, operation):
        document_id = metadata["id"]
        directory = self.storage.path(document_id)
        previous_version = metadata["current_version"]
        metadata.update(state="fixing" if operation == "fix" else "checking", last_error=None)
        self.storage.write(metadata)
        work = Path(tempfile.mkdtemp(prefix=".work-", dir=directory))
        try:
            await self.runner.run(directory / "original.docx", work, operation)
            # Independently validate the result before making it visible.
            report = json.loads((work / "report.json").read_text(encoding="utf-8"))
            validate_report(report)
            if self.storage.usage() > self.settings.max_storage_bytes:
                raise ProcessingError("storage_full", "Недостаточно места для сохранения результата.", 507)
            version = "corrected" if operation == "fix" else "original"
            if operation == "fix":
                os.replace(work / "corrected.docx", directory / "corrected.docx")
            os.replace(work / "report.json", directory / f"{version}-report.json")
            if (work / "warnings.json").exists():
                os.replace(work / "warnings.json", directory / f"{version}-warnings.json")
            metadata.update(state="fixed" if operation == "fix" else "checked", current_version=version)
            self.storage.write(metadata)
        except asyncio.CancelledError:
            metadata.update(state="failed", current_version=previous_version, last_error={"code": "interrupted", "message": "Обработка прервана. Повторите попытку."})
            self.storage.write(metadata)
            raise
        except Exception as exc:
            error = exc if isinstance(exc, ProcessingError) else ProcessingError("processing_failed", "Не удалось сохранить или проверить результат. Исходный файл сохранён.")
            if error is not exc:
                LOG.exception("Failed to publish result for %s", document_id)
            metadata.update(state="failed", current_version=previous_version, last_error={"code": error.code, "message": error.message})
            self.storage.write(metadata)
            raise error from exc
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def begin_download(self, document_id, owner_id):
        metadata = self.access(document_id, owner_id)
        if metadata["current_version"] != "corrected":
            raise ProcessingError("not_ready", "Проверенная исправленная копия ещё не готова.", 409)
        stream = (self.storage.path(document_id) / "corrected.docx").open("rb")
        return metadata, stream

    def end_download(self, document_id, stream):
        if not stream.closed:
            stream.close()
