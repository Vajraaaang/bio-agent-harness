"""Execution adapters with lazy, explicitly requested benchmark exports."""

__all__ = ["EvalResult", "EvalRunner"]


def __getattr__(name):
    if name not in __all__:
        raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
    from devbench_engine import runner
    value = getattr(runner, name)
    globals()[name] = value
    return value
