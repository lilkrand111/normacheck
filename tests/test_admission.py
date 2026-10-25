"""Admission, proxy identity and interrupted multipart requests use no real documents."""
import asyncio
from dataclasses import replace
from io import BytesIO
import secrets

from docx import Document
from fastapi.testclient import TestClient
import pytest
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.config import Settings
from app.main import create_app
from app.rate_limit import RateLimiter
from app.runner import ProcessingError


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


@pytest.fixture
def settings(tmp_path):
    return Settings(storage_dir=tmp_path / "storage", allowed_hosts=("testserver",))


def authorize(client):
    client.headers["X-CSRF-Token"] = client.get("/api/session").json()["csrf_token"]


def payload():
    document = Document()
    document.add_paragraph("Тестовый документ для проверки допуска запросов.")
    output = BytesIO()
    document.save(output)
    return output.getvalue()


def test_bucket_burst_refill_and_bounded_client_table(settings):
    clock = Clock()
    limiter = RateLimiter(replace(settings, rate_limit_per_minute=12, rate_limit_burst=2,
                                  rate_limit_max_clients=2), clock=clock)
    limiter.check("192.0.2.1")
    limiter.check("::ffff:192.0.2.1")  # Same IPv4 address must share its allowance.
    with pytest.raises(ProcessingError) as limited:
        limiter.check("192.0.2.1")
    assert limited.value.status_code == 429 and limited.value.retry_after == 5
    limiter.check("192.0.2.2")
    with pytest.raises(ProcessingError):
        limiter.check("192.0.2.3")
    assert len(limiter.buckets) == 2
    clock.now = 5
    limiter.check("192.0.2.1")
    with pytest.raises(ProcessingError):
        limiter.check("192.0.2.1")
    clock.now = 16
    limiter.check("192.0.2.3")
    assert list(limiter.buckets) == ["192.0.2.3"]


def test_upload_and_fix_share_limit_without_affecting_reads_or_storage(settings):
    clock = Clock()
    with TestClient(create_app(replace(settings, rate_limit_burst=2))) as client:
        client.app.state.rate_limiter.clock = clock
        authorize(client)
        data = payload()
        response = client.post("/api/documents", files={"file": ("test.docx", data)})
        assert response.status_code == 201
        document_id = response.json()["document_id"]
        assert client.post(f"/api/documents/{document_id}/fix").status_code == 200
        storage = client.app.state.service.storage
        before = {path: path.read_bytes() for path in storage.documents.rglob("*") if path.is_file()}
        rejected = client.post("/api/documents", files={"file": ("extra.docx", data)})
        assert rejected.status_code == 429
        assert rejected.headers["retry-after"] == "5"
        assert rejected.headers["cache-control"] == "no-store"
        assert rejected.json()["error"]["code"] == "rate_limited"
        assert not client.app.state.service.runner.processes
        assert before == {path: path.read_bytes() for path in storage.documents.rglob("*") if path.is_file()}
        assert client.get(f"/api/documents/{document_id}").status_code == 200
        assert client.get(f"/api/documents/{document_id}/download").status_code == 200
        assert client.get("/health").status_code == 200
        client.cookies.clear()
        authorize(client)
        assert client.post("/api/documents").status_code == 429
        clock.now = 5
        assert client.post("/api/documents", files={"file": ("second.docx", data)}).status_code == 201


@pytest.mark.parametrize("peer,trusted", [("172.30.76.2", True), ("192.0.2.10", False)])
def test_proxy_headers_only_change_identity_for_trusted_peer(settings, peer, trusted):
    application = create_app(replace(settings, rate_limit_burst=1))
    proxy = ProxyHeadersMiddleware(application, trusted_hosts="172.30.76.2")
    with TestClient(proxy, client=(peer, 12345)) as client:
        application.state.rate_limiter.clock = Clock()
        authorize(client)
        # Empty multipart-independent requests spend a token but create no files.
        assert client.post("/api/documents", headers={"X-Forwarded-For": "198.51.100.1"}).status_code == 422
        response = client.post("/api/documents", headers={"X-Forwarded-For": "198.51.100.2"})
        assert response.status_code == (422 if trusted else 429)
        assert client.post("/api/documents", headers={"X-Forwarded-For": "198.51.100.1"}).status_code == 429
        assert not list(application.state.service.storage.documents.iterdir())


def test_invalid_csrf_does_not_spend_tokens(settings):
    with TestClient(create_app(replace(settings, rate_limit_burst=1))) as client:
        client.app.state.rate_limiter.clock = Clock()
        authorize(client)
        assert client.post("/api/documents", headers={"X-CSRF-Token": "wrong"}).status_code == 403
        assert client.post("/api/documents").status_code == 422
        assert client.post("/api/documents").status_code == 429


@pytest.mark.parametrize("failure", ["total", "idle", "disconnect", "size"])
def test_interrupted_multipart_closes_spooled_file_and_releases_slot(settings, monkeypatch, failure):
    import app.middleware as middleware
    import starlette.formparsers as formparsers

    clock = Clock()
    monkeypatch.setattr(middleware, "monotonic", clock)
    files = []
    factory = formparsers.SpooledTemporaryFile

    def tracked_file(*args, **kwargs):
        file = factory(*args, **kwargs)
        files.append(file)
        return file

    monkeypatch.setattr(formparsers, "SpooledTemporaryFile", tracked_file)

    async def exercise():
        application = create_app(replace(settings, upload_timeout=3, upload_idle_timeout=0.01))
        async with application.router.lifespan_context(application):
            owner = secrets.token_urlsafe(32)
            sessions = application.state.sessions
            cookie = "docx_session=" + sessions.serializer.dumps(owner)
            scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                     "method": "POST", "scheme": "http", "path": "/api/documents", "query_string": b"",
                     "headers": [(b"host", b"testserver"), (b"content-type", b"multipart/form-data; boundary=test"),
                                 (b"cookie", cookie.encode()), (b"x-csrf-token", sessions.token(owner).encode())],
                     "client": ("192.0.2.1", 123), "server": ("testserver", 80)}
            reads = 0
            output = []

            async def receive():
                nonlocal reads
                reads += 1
                if reads == 1:
                    clock.now = 2
                    body = b'--test\r\nContent-Disposition: form-data; name="file"; filename="test.docx"\r\n\r\n'
                    return {"type": "http.request", "body": body + b"x" * (1024 * 1024 + 1), "more_body": True}
                if failure == "idle":
                    await asyncio.Event().wait()
                if failure == "disconnect":
                    return {"type": "http.disconnect"}
                if failure == "size":
                    return {"type": "http.request", "body": b"x" * (21 * 1024 * 1024), "more_body": True}
                clock.now = 4
                return {"type": "http.request", "body": b"x", "more_body": True}

            async def send(message):
                output.append(message)

            if failure == "disconnect":
                # ClientDisconnect propagates; the request must still release resources.
                from starlette.requests import ClientDisconnect
                with pytest.raises(ClientDisconnect):
                    await application(scope, receive, send)
            else:
                await application(scope, receive, send)
                status = next(item["status"] for item in output if item["type"] == "http.response.start")
                assert status == (413 if failure == "size" else 408)
            assert reads == 2
            assert files and all(file.closed for file in files)
            assert not list(application.state.service.storage.documents.iterdir())
            service = application.state.service
            async with service.slot(), service.slot():
                with pytest.raises(ProcessingError) as busy:
                    async with service.slot():
                        pytest.fail("A third slot must not be available")
                assert busy.value.status_code == 503

    asyncio.run(exercise())


@pytest.mark.parametrize("name", ["processing_timeout", "upload_timeout", "upload_idle_timeout", "rate_limit_per_minute"])
@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan")])
def test_invalid_timing_and_rate_settings_are_rejected(settings, name, value):
    with pytest.raises(ValueError):
        replace(settings, **{name: value})
