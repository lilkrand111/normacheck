"""Concurrent subprocesses with wall-clock timeouts and cancellation cleanup."""
import asyncio
import json
import logging
import os
import sys

from .config import ROOT

LOG = logging.getLogger(__name__)


class ProcessingError(Exception):
    def __init__(self, code, message, status_code=500, *, retry_after=None):
        self.code, self.message, self.status_code = code, message, status_code
        self.retry_after = retry_after
        super().__init__(message)


class Runner:
    def __init__(self, settings):
        self.settings = settings
        self.processes = set()

    async def run(self, source, work, operation):
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "app.worker", operation, str(source), str(work),
            "--parent-pid", str(os.getpid()),
            "--memory-mb", str(self.settings.worker_memory_mb),
            "--timeout", str(self.settings.processing_timeout),
            cwd=ROOT, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        )
        self.processes.add(process)
        try:
            try:
                _, stderr = await asyncio.wait_for(process.communicate(), timeout=self.settings.processing_timeout)
            except asyncio.TimeoutError:
                raise ProcessingError("timeout", "Превышено время обработки. Попробуйте документ меньшего размера.", 504) from None
            if process.returncode != 0:
                LOG.error("Worker exited with status %s", process.returncode)
                raise ProcessingError("worker_failed", "Обработка прервана. Возможно, документ требует слишком много ресурсов.")
            try:
                result = json.loads((work / "result.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raise ProcessingError("worker_failed", "Не удалось получить результат обработки.") from None
            if not result.get("ok"):
                LOG.warning("Worker failed: %s; %s", result.get("code"), stderr.decode(errors="replace")[-4000:])
                raise ProcessingError(result["code"], result["message"], 422 if result["code"] == "invalid_document" else 500)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
            self.processes.discard(process)

    async def close(self):
        processes = list(self.processes)
        for process in processes:
            if process.returncode is None:
                process.kill()
        await asyncio.gather(*(process.wait() for process in processes))
