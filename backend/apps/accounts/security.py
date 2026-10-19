"""Límite de intentos de login por cuenta y por IP (contadores en caché)."""

import hashlib

from django.conf import settings
from django.core.cache import cache

DEFAULT_MAX_FAILURES = 5
DEFAULT_IP_MAX_FAILURES = 20
DEFAULT_LOCKOUT_SECONDS = 15 * 60


def _setting(name, default):
    return getattr(settings, name, default)


def lockout_seconds():
    return _setting("LOGIN_LOCKOUT_SECONDS", DEFAULT_LOCKOUT_SECONDS)


def client_ip(request):
    """IP del cliente. Solo confía en X-Forwarded-For si se declaran proxies propios."""
    remote = request.META.get("REMOTE_ADDR", "")
    proxies = _setting("TRUSTED_PROXY_COUNT", 0)
    if proxies > 0:
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        parts = [part.strip() for part in forwarded.split(",") if part.strip()]
        if len(parts) >= proxies:
            return parts[-proxies]
    return remote


def _account_key(email):
    digest = hashlib.sha256(email.encode()).hexdigest()
    return f"login-fail:account:{digest}"


def _ip_key(ip):
    return f"login-fail:ip:{ip}"


def is_blocked(email, ip):
    max_account = _setting("LOGIN_MAX_FAILURES", DEFAULT_MAX_FAILURES)
    max_ip = _setting("LOGIN_IP_MAX_FAILURES", DEFAULT_IP_MAX_FAILURES)
    return (
        cache.get(_account_key(email), 0) >= max_account
        or cache.get(_ip_key(ip), 0) >= max_ip
    )


def register_failure(email, ip):
    for key in (_account_key(email), _ip_key(ip)):
        cache.add(key, 0, timeout=lockout_seconds())
        try:
            cache.incr(key)
        except ValueError:  # la clave venció entre add e incr
            cache.set(key, 1, timeout=lockout_seconds())


def clear_failures(email):
    cache.delete(_account_key(email))


def should_log_block(email):
    """True solo la primera vez por ventana de bloqueo, para no inundar la auditoría."""
    key = _account_key(email).replace("login-fail", "login-block-logged")
    return cache.add(key, 1, timeout=lockout_seconds())
