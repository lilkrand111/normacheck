"""Bounded, process-local token buckets for costly mutation requests."""
from collections import OrderedDict
from ipaddress import ip_address
import math
from threading import Lock
from time import monotonic

from .runner import ProcessingError


class RateLimiter:
    def __init__(self, settings, *, clock=monotonic):
        self.capacity = settings.rate_limit_burst
        self.refill = settings.rate_limit_per_minute / 60
        self.max_clients = settings.rate_limit_max_clients
        self.clock = clock
        self.buckets = OrderedDict()
        self.lock = Lock()

    def check(self, host):
        # ProxyHeadersMiddleware supplies the client only for trusted peers.
        # Never read X-Forwarded-For here. Normalize equivalent IP spellings.
        try:
            address = ip_address(host)
            key = str(getattr(address, "ipv4_mapped", None) or address)
        except ValueError:
            key = host
        with self.lock:
            now = self.clock()
            lifetime = self.capacity / self.refill
            while self.buckets:
                oldest, (_, updated) = next(iter(self.buckets.items()))
                if now - updated < lifetime:
                    break
                del self.buckets[oldest]
            if key not in self.buckets:
                if len(self.buckets) >= self.max_clients:
                    # Do not evict active clients: cycling IPs must not reset them.
                    updated = next(iter(self.buckets.values()))[1]
                    self._reject(updated + lifetime - now)
                tokens, updated = self.capacity, now
            else:
                tokens, updated = self.buckets.pop(key)
            tokens = min(self.capacity, tokens + (now - updated) * self.refill)
            allowed = tokens >= 1
            self.buckets[key] = (tokens - 1 if allowed else tokens, now)
            if not allowed:
                self._reject((1 - tokens) / self.refill)

    @staticmethod
    def _reject(seconds):
        raise ProcessingError("rate_limited", "Слишком много запросов. Повторите действие немного позже.",
                              429, retry_after=max(1, math.ceil(seconds)))
