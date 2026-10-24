"""Signed anonymous sessions and explicit CSRF tokens, without server sessions."""
import hashlib
import hmac
import secrets

from itsdangerous import BadSignature, URLSafeTimedSerializer

from .runner import ProcessingError

COOKIE = "docx_session"
SESSION_AGE = 7 * 24 * 3600


class Sessions:
    def __init__(self, secret):
        self.secret = secret.encode()
        self.serializer = URLSafeTimedSerializer(secret, salt="docx-session-v1")

    def owner(self, request, required=True):
        try:
            owner = self.serializer.loads(request.cookies.get(COOKIE, ""), max_age=SESSION_AGE)
            if not isinstance(owner, str) or len(owner) != 43:
                raise BadSignature("Invalid owner")
            return owner
        except BadSignature:
            if required:
                raise ProcessingError("session_required", "Обновите страницу, чтобы начать сессию.", 401) from None
            return None

    def establish(self, request, response, secure):
        owner = self.owner(request, required=False) or secrets.token_urlsafe(32)
        response.set_cookie(COOKIE, self.serializer.dumps(owner), max_age=SESSION_AGE,
                            httponly=True, secure=secure, samesite="lax", path="/")
        return owner

    def token(self, owner):
        return hmac.new(self.secret, ("csrf:" + owner).encode(), hashlib.sha256).hexdigest()

    def authorize_mutation(self, request):
        owner = self.owner(request)
        if not hmac.compare_digest(request.headers.get("x-csrf-token", ""), self.token(owner)):
            raise ProcessingError("csrf", "Обновите страницу и повторите действие.", 403)
        return owner
