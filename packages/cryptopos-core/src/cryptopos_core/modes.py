"""The complete mode vocabulary, shared by every money boundary.

A misspelling of ``mainnet`` must not become a permissive demo mode. Keeping
the vocabulary and its real-money subset here means pricing, address checks,
token selection and URI construction cannot interpret the same string in
different directions.
"""

from .errors import InvalidMode

DEMO = "demo"
TESTNET = "testnet"
MAINNET = "mainnet"

VALID_MODES = (DEMO, TESTNET, MAINNET)
REAL_MONEY_MODES = (MAINNET,)


def require_mode(mode):
	"""Return a known mode or raise before policy can be weakened by a typo."""
	if mode not in VALID_MODES:
		raise InvalidMode(mode, VALID_MODES)
	return mode


def address_network(mode):
	"""Network-bearing addresses use mainnet only for explicit real money."""
	require_mode(mode)
	return MAINNET if mode == MAINNET else TESTNET
