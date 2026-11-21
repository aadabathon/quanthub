"""Suite-wide setup.

Installed extensions (``quant_hub/extensions.py``, D86) are off for every
test. The suite then checks the app a fresh clone gets, whatever else is
installed on this machine. The hook's own tests pass stand-in entry points,
or clear the variable themselves.
"""

import os

from quant_hub.extensions import DISABLE_ENV

os.environ[DISABLE_ENV] = "1"
