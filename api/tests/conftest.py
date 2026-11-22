from collections.abc import Iterator

import pytest

from api.config import get_settings


@pytest.fixture(autouse=True)
def isolate_settings() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
