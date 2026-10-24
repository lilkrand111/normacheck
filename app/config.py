"""Configuration for the API and its isolated document workers."""
from dataclasses import dataclass, field
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    storage_dir: Path = field(default_factory=lambda: Path(os.getenv("STORAGE_DIR", ROOT / "storage")).resolve())
    secret: str = field(default_factory=lambda: os.getenv("APP_SECRET", ""))
    cookie_secure: bool = field(default_factory=lambda: os.getenv("COOKIE_SECURE", "false").lower() == "true")
    allowed_hosts: tuple = field(default_factory=lambda: tuple(filter(None, os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1").split(","))))
    max_upload_bytes: int = field(default_factory=lambda: int(os.getenv("MAX_UPLOAD_MB", "20")) * 1024 * 1024)
    max_storage_bytes: int = field(default_factory=lambda: int(os.getenv("MAX_STORAGE_MB", "2048")) * 1024 * 1024)
    max_concurrent_jobs: int = field(default_factory=lambda: int(os.getenv("MAX_CONCURRENT_JOBS", "2")))
    processing_timeout: float = field(default_factory=lambda: float(os.getenv("PROCESSING_TIMEOUT_SECONDS", "60")))
    worker_memory_mb: int = field(default_factory=lambda: int(os.getenv("WORKER_MEMORY_MB", "768")))
    ruleset_version: str = "stp-01-2017-2026-10-22"

    def __post_init__(self):
        for name in ("max_upload_bytes", "max_storage_bytes", "max_concurrent_jobs", "processing_timeout", "worker_memory_mb"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if self.secret and len(self.secret) < 32:
            raise ValueError("APP_SECRET must contain at least 32 characters")
        if self.cookie_secure and not self.secret:
            raise ValueError("Set APP_SECRET when COOKIE_SECURE=true")
