"""Request-equivalent checkout lifetime for Native/LangGraph ORM threads.

Executor threads have no Django request_finished signal. Release their wrappers
at an operation boundary, while leaving an enclosing caller transaction alone.
Nesting belongs to the physical thread, not an async ContextVar propagated into
another executor thread, whose Django wrapper is independently owned.
"""
from contextlib import contextmanager
from functools import wraps
from threading import local

from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import connections

_state = local()


def _wrappers():
    return connections.all(initialized_only=True) if settings.configured else ()


@contextmanager
def database_operation():
    if getattr(_state, "depth", 0):
        _state.depth += 1
        try:
            yield
        finally:
            _state.depth -= 1
        return
    _state.depth = 1
    protected = set()
    error = None
    try:
        for wrapper in _wrappers():
            if wrapper.in_atomic_block or (wrapper.connection is not None and not wrapper.get_autocommit()):
                protected.add(id(wrapper))
            else:
                wrapper.close_if_unusable_or_obsolete()
        yield
    except BaseException as caught:
        error = caught
        raise
    finally:
        try:
            for wrapper in _wrappers():
                if id(wrapper) not in protected and not wrapper.in_atomic_block:
                    wrapper.close()  # native Django pool putconn; never close_pool.
        except Exception as cleanup_error:
            if error is None:
                raise
            # Keep the actual business/auth exception, rather than replacing
            # it with cleanup. A failed operation never becomes successful.
            error.add_note("Agent ORM checkout cleanup failed: " + type(cleanup_error).__name__)
        finally:
            _state.depth = 0


def database_boundary(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with database_operation():
            return function(*args, **kwargs)
    return wrapped


def database_sync_to_async(function=None, **kwargs):
    if function is None:
        return lambda method: database_sync_to_async(method, **kwargs)
    return sync_to_async(database_boundary(function), **kwargs)
