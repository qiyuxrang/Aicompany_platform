"""One bounded PostgreSQL policy for Portal and owned acceptance fixtures.

Limits apply to one process, not to each WSGI thread. Django's request-boundary
close returns the connection to this pool; it must retain CONN_MAX_AGE=0.
"""
from importlib.metadata import version

from django.core.exceptions import ImproperlyConfigured


POOL_OPTIONS = {"min_size": 1, "max_size": 4, "timeout": 5.0, "max_waiting": 16}
# Pool checkout timeout doesn't interrupt a background connection handshake.
CONNECT_TIMEOUT_SECONDS = 3
POOL_STATS = ("pool_min", "pool_max", "pool_size", "pool_available", "requests_waiting",
              "requests_num", "requests_queued", "requests_errors", "requests_wait_ms",
              "usage_ms", "connections_num", "connections_errors", "connections_ms")


def postgres_database(*, name, user, password, host="db", port="5432"):
    try:
        import psycopg_pool  # noqa: F401: fail at configuration, not first HTTP request.
    except ImportError as error:
        raise ImproperlyConfigured("PostgreSQL requires the locked psycopg pool dependency") from error
    return {"ENGINE": "django.db.backends.postgresql", "NAME": name, "USER": user,
            "PASSWORD": password, "HOST": host, "PORT": port, "CONN_MAX_AGE": 0,
            "CONN_HEALTH_CHECKS": True,
            "OPTIONS": {"pool": dict(POOL_OPTIONS), "connect_timeout": CONNECT_TIMEOUT_SECONDS}}


def postgres_pool_evidence(wrapper):
    """Expose only verified public bounds and numeric stats; never conninfo."""
    if wrapper.vendor != "postgresql":
        return {"enabled": False, "scope": "not_postgresql"}
    configuration = wrapper.settings_dict
    connect_timeout = configuration.get("OPTIONS", {}).get("connect_timeout")
    if configuration.get("CONN_MAX_AGE") != 0 or configuration.get("CONN_HEALTH_CHECKS") is not True or \
            configuration.get("OPTIONS", {}).get("pool") != POOL_OPTIONS or \
            type(connect_timeout) is not int or connect_timeout != CONNECT_TIMEOUT_SECONDS:
        raise ImproperlyConfigured("PostgreSQL pool configuration differs from Portal bounds")
    pool = wrapper.pool
    if pool is None:
        raise ImproperlyConfigured("PostgreSQL connection pool is unavailable")
    stats = pool.get_stats()
    actual_kwargs = pool.kwargs
    actual_connect_timeout = actual_kwargs.get("connect_timeout") if isinstance(actual_kwargs, dict) else None
    if stats.get("pool_min") != POOL_OPTIONS["min_size"] or stats.get("pool_max") != POOL_OPTIONS["max_size"] or \
            pool.timeout != POOL_OPTIONS["timeout"] or pool.max_waiting != POOL_OPTIONS["max_waiting"] or \
            type(actual_connect_timeout) is not int or actual_connect_timeout != CONNECT_TIMEOUT_SECONDS:
        raise ImproperlyConfigured("Actual PostgreSQL pool differs from Portal bounds")
    return {"enabled": True, "scope": "per_process", "implementation": "django_psycopg3",
            "version": version("psycopg-pool"), "min_size": stats["pool_min"], "max_size": stats["pool_max"],
            "timeout_seconds": pool.timeout, "max_waiting": pool.max_waiting,
            "connect_timeout_seconds": actual_connect_timeout,
            "conn_max_age": configuration["CONN_MAX_AGE"], "health_checks": configuration["CONN_HEALTH_CHECKS"],
            "stats": {key: stats[key] for key in POOL_STATS if type(stats.get(key)) in (int, float)}}
