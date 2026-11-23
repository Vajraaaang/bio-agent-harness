"""Explicit prerequisites for integration tests that exercise wheel custody."""
from importlib.metadata import distribution
import json

import pytest


def require_product_wheel():
    direct_url = distribution("devharness").read_text("direct_url.json")
    if direct_url and json.loads(direct_url).get("dir_info", {}).get("editable") is True:
        pytest.skip("Requires an ordinary wheel installation for RECORD custody")
