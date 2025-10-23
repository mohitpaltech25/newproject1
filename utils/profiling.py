import os
import time
import inspect
import functools
from utils.logger_config import logger

PROFILE_ENABLED = os.getenv("PROFILE_ENABLED", "1") not in ("0", "false", "False")


def _log_duration(func, start):
    duration_ms = (time.perf_counter() - start) * 1000.0
    logger.info(f"[PROFILE] {func.__module__}.{func.__qualname__} took {duration_ms:.2f} ms")


def profile(func):
    """Decorator to log execution time of sync, async, and generator functions."""
    if inspect.iscoroutinefunction(func):
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            if not PROFILE_ENABLED:
                return await func(*args, **kwargs)
            start = time.perf_counter()
            try:
                return await func(*args, **kwargs)
            finally:
                _log_duration(func, start)
        return async_wrapper

    if inspect.isgeneratorfunction(func):
        @functools.wraps(func)
        def gen_wrapper(*args, **kwargs):
            if not PROFILE_ENABLED:
                yield from func(*args, **kwargs)
                return
            start = time.perf_counter()
            try:
                for item in func(*args, **kwargs):
                    yield item
            finally:
                _log_duration(func, start)
        return gen_wrapper

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        if not PROFILE_ENABLED:
            return func(*args, **kwargs)
        start = time.perf_counter()
        try:
            return func(*args, **kwargs)
        finally:
            _log_duration(func, start)
    return wrapper


def profile_class(cls):
    """Class decorator to apply @profile to all methods (incl. static/class methods)."""
    for name, attr in list(vars(cls).items()):
        if name.startswith("__") and name.endswith("__"):
            continue
        # Skip attributes that are not callables
        if isinstance(attr, staticmethod):
            func = attr.__func__
            setattr(cls, name, staticmethod(profile(func)))
        elif isinstance(attr, classmethod):
            func = attr.__func__
            setattr(cls, name, classmethod(profile(func)))
        elif inspect.isfunction(attr):
            setattr(cls, name, profile(attr))
        # properties and other descriptors are ignored
    return cls
