"""Test suite for cryptopos-core.

This exists as a package for one reason: discovery imports it before any test
module, which makes it the only place to put the src-layout path fix. Without
it `python -m unittest discover` from the package root finds the tests and
then cannot import the thing they test.

The fix is conditional on purpose. When the package is INSTALLED -- a wheel in
a clean venv, which is how a user gets it -- nothing is inserted and the tests
exercise the installed copy rather than the working tree. That is the run that
actually proves the distribution works.
"""

import sys
from pathlib import Path

try:
	import cryptopos_core  # noqa: F401
except ImportError:
	sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
