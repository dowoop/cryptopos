"""cryptopos-core — installable payment rails that owe nothing to a framework.

The public surfaces separate mechanism from host policy:

    plugin   concrete network/asset identities and immutable rail values
    registry explicit built-in and installed entry-point discovery
    catalog  every built-in rail, including honest partial capability reports
    bitcoin  verified Bitcoin Testnet 4 observation and settlement
    evm      verified Sepolia and Amoy observation and settlement
    ootle    final balance observation without false transaction attribution

    rates    quoting an asset in microcents, under stricter rules when the
             money is real; converting cents to exact native units
    addresses checksum-verifying a receiving address and binding it to the
             sale's network -- the last check before money moves
    rails    what the terminal knows about each chain, as one table of pure
             data, plus the integer unit math that reads it
    uri      a payment URI per scheme — the exact string the QR encodes
    qr       a payment URI as a module grid, ready to draw
    chain    reading the Ootle policy tier — the promise, the ceilings, a
             balance — without an account and without a fee
    errors   documented refusals at pricing, provider, and payment boundaries

What is NOT here is deliberate: persistence, scheduling, permissions, and the
sale's state machine. Those are where a host framework is genuinely better
than a library, and a POS that hid them inside a package would be fighting
whatever it was embedded in.

    >>> from cryptopos_core.registry import RailRegistry
    >>> registry = RailRegistry()
    >>> len(registry.register_builtins())
    12

The chain reader takes its configuration at construction, so it reads the
same chain from a till, a web backend, or a script with nothing under it:

    >>> from cryptopos_core.chain import OotleReader
    >>> reader = OotleReader(loyalty_component="component_abc...")
    >>> facts, reason = reader.promise()
"""

from . import (
	addresses,
	bitcoin,
	catalog,
	chain,
	conformance,
	errors,
	evm,
	modes,
	ootle,
	plugin,
	qr,
	qrcodegen,
	rails,
	rates,
	registry,
	uri,
)
from .chain import OotleReader, ceilings_wording, earning_only_notice
from .errors import (
	AddressRefused,
	AmountNotRepresentable,
	CryptoPosError,
	DuplicateRail,
	FeedsDisagree,
	InvalidAmount,
	InvalidAsset,
	InvalidMode,
	InvalidPaymentIdentity,
	InvalidRailPlugin,
	InvalidRate,
	RailNotInstalled,
	RailPluginError,
	RailProviderError,
	RateUnavailable,
	UnsupportedCapability,
	UnsupportedRail,
)
from .modes import VALID_MODES
from .rails import RAILS, rail_for, rail_keys
from .uri import build_uri

__version__ = "1.1.0"

__all__ = [
	"RAILS",
	"VALID_MODES",
	"AddressRefused",
	"AmountNotRepresentable",
	"CryptoPosError",
	"DuplicateRail",
	"FeedsDisagree",
	"InvalidAmount",
	"InvalidAsset",
	"InvalidMode",
	"InvalidPaymentIdentity",
	"InvalidRailPlugin",
	"InvalidRate",
	"OotleReader",
	"RailNotInstalled",
	"RailPluginError",
	"RailProviderError",
	"RateUnavailable",
	"UnsupportedCapability",
	"UnsupportedRail",
	"__version__",
	"addresses",
	"bitcoin",
	"build_uri",
	"catalog",
	"ceilings_wording",
	"chain",
	"conformance",
	"earning_only_notice",
	"errors",
	"evm",
	"modes",
	"ootle",
	"plugin",
	"qr",
	"qrcodegen",
	"rail_for",
	"rail_keys",
	"rails",
	"rates",
	"registry",
	"uri",
]
