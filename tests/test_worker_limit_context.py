"""Pure deadline/budget configuration checks; no worker is spawned."""
import time
import pytest
from devharness_core import watchdog


def helpers():
    context=getattr(watchdog,"worker_limits",None)
    resolve=getattr(watchdog,"_effective_worker_limits",None)
    assert callable(context) and callable(resolve), "Coordinator limits must reach numerical workers"
    return context,resolve


def test_context_caps_worker_limits_and_restores_previous_settings():
    context,resolve=helpers()
    with context(deadline=time.monotonic()+20,max_mem_mb=512):
        memory,timeout,growth=resolve(8000,300,1500)
        assert memory==512
        assert 0 < timeout <=20
        assert growth==1500
    assert resolve(8000,300,1500)==(8000,300,1500)


def test_nested_context_cannot_relax_the_parent_limit():
    context,resolve=helpers()
    with context(deadline=time.monotonic()+10,max_mem_mb=256):
        with context(deadline=time.monotonic()+100,max_mem_mb=4096):
            memory,timeout,_=resolve(8000,300,1500)
            assert memory==256
            assert timeout<=10


def test_expired_limit_rejects_before_a_worker_can_start():
    context,resolve=helpers()
    with context(deadline=time.monotonic()-1,max_mem_mb=256):
        with pytest.raises(watchdog.HarnessResourceInterrupt,match="deadline"):
            resolve(8000,300,1500)
