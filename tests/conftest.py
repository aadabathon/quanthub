"""Suite-wide setup.

Installed extensions (``quant_hub/extensions.py``, D86) are off for every
test. The suite then checks the app a fresh clone gets, whatever else is
installed on this machine. The hook's own tests pass stand-in entry points,
or clear the variable themselves.
"""

import os

import pytest

from quant_hub.extensions import DISABLE_ENV

os.environ[DISABLE_ENV] = "1"


@pytest.fixture
def parts(tmp_path):
    """The desk's parts over a made-up book (``tests/desk_fixtures.py``):
    the activity service, an empty desk store, and the folder."""
    from desk_fixtures import build_parts
    activity, store, closers = build_parts(tmp_path)
    yield activity, store, tmp_path
    for s in closers:
        s.close()
