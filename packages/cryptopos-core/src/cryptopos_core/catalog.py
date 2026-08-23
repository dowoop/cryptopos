"""Built-in concrete test-network catalog, with capability gaps made explicit.

These adapters preserve the existing safe address and payment-request work
while provider-specific observers move into independent rail plugins. They do
not simulate observation or settlement: a rail that can build a QR but cannot
prove receipt is request-ready, not charge-ready.
"""

from .addresses import validate
from .bitcoin import bitcoin_testnet4
from .errors import AddressRefused, InvalidRailPlugin, UnsupportedCapability
from .evm import ethereum_sepolia, usdc_ethereum_sepolia, usdc_polygon_amoy
from .ootle import ootle_esmeralda
from .plugin import (
	ADDRESS_VALIDATION,
	OBSERVATION,
	PAYMENT_REQUEST,
	SETTLEMENT,
	Asset,
	Network,
	PaymentIntent,
	PaymentRequest,
	Readiness,
)
from .rails import USDC_MINT_DEVNET
from .uri import build_uri


class RequestRail:
	"""A concrete test-network adapter for verified request builders."""

	def __init__(
		self,
		legacy_key,
		network,
		asset,
		*,
		request_ready=True,
		address_validation_ready=True,
		blocker,
		payer_notice="",
	):
		self.legacy_key = legacy_key
		self.network = network
		self.asset = asset
		self.key = f"{network.key}/{asset.key}"
		self.blocker = blocker
		self.payer_notice = payer_notice
		self.address_validation_ready = address_validation_ready
		capabilities = set()
		if address_validation_ready:
			capabilities.add(ADDRESS_VALIDATION)
		if request_ready:
			capabilities.add(PAYMENT_REQUEST)
		self.capabilities = frozenset(capabilities)

	def readiness(self, configuration):
		unavailable = [(OBSERVATION, self.blocker), (SETTLEMENT, "settlement needs trustworthy observations")]
		if ADDRESS_VALIDATION not in self.capabilities:
			unavailable.insert(0, (ADDRESS_VALIDATION, self.blocker))
		if PAYMENT_REQUEST not in self.capabilities:
			unavailable.insert(0, (PAYMENT_REQUEST, self.blocker))
		return Readiness(self.key, self.capabilities, tuple(unavailable))

	def validate_recipient(self, recipient):
		if not self.address_validation_ready:
			return "refused", self.blocker
		return validate(self.legacy_key, recipient, "testnet")

	def capture_baseline(self, recipient, configuration):
		raise UnsupportedCapability(self.key, OBSERVATION)

	def create_request(self, intent):
		self._intent(intent)
		if PAYMENT_REQUEST not in self.capabilities:
			raise UnsupportedCapability(self.key, PAYMENT_REQUEST)
		verdict, reason = self.validate_recipient(intent.recipient)
		if verdict == "refused":
			raise AddressRefused(self.legacy_key, intent.recipient, verdict, reason)
		identity = {"address": intent.recipient}
		if self.legacy_key in ("sol", "usdc-sol"):
			identity["reference"] = intent.payment_reference
		uri = build_uri(self.legacy_key, identity, intent.amount_native, "testnet")
		return PaymentRequest(self.key, uri, intent.recipient, intent.amount_native, self.payer_notice)

	def observe(self, intent, configuration, previous=None):
		raise UnsupportedCapability(self.key, OBSERVATION)

	def settle(self, intent, observations, claimed_transaction_ids=frozenset()):
		raise UnsupportedCapability(self.key, SETTLEMENT)

	def _intent(self, intent):
		if not isinstance(intent, PaymentIntent) or intent.rail_key != self.key:
			raise InvalidRailPlugin("payment intent belongs to another rail")


_OBSERVER_NOT_EXTRACTED = "the provider-specific observer has not been extracted into this package"

polygon_amoy = RequestRail(
	"pol",
	Network("polygon", "amoy", True),
	Asset("native", "pol", "AmoyPOL", 18),
	blocker=_OBSERVER_NOT_EXTRACTED,
)
solana_devnet = RequestRail(
	"sol",
	Network("solana", "devnet", True),
	Asset("native", "sol", "DevnetSOL", 9),
	blocker=_OBSERVER_NOT_EXTRACTED,
	payer_notice="Configure the payer wallet for Solana devnet; Solana Pay does not encode a cluster.",
)
usdc_solana_devnet = RequestRail(
	"usdc-sol",
	Network("solana", "devnet", True),
	Asset("spl", USDC_MINT_DEVNET, "USDC", 6),
	blocker=_OBSERVER_NOT_EXTRACTED,
	payer_notice="Configure the payer wallet for Solana devnet; Solana Pay does not encode a cluster.",
)
monero_stagenet = RequestRail(
	"xmr",
	Network("monero", "stagenet", True),
	Asset("native", "xmr", "StagenetXMR", 12),
	request_ready=False,
	address_validation_ready=False,
	blocker="the legacy validator cannot yet express Monero stagenet separately from testnet",
)
minotari_esmeralda = RequestRail(
	"xtm",
	Network("minotari", "esmeralda", True),
	Asset("native", "xtm", "EsmeraldaXTM", 6),
	blocker="Minotari observation requires the wallet or base-node gRPC transport",
)
dash_testnet = RequestRail(
	"dash",
	Network("dash", "testnet", True),
	Asset("native", "dash", "TDASH", 8),
	blocker="the Insight observer is not extracted and cannot prove Dash ChainLocks",
)
zcash_testnet = RequestRail(
	"zec",
	Network("zcash", "testnet", True),
	Asset("native", "zec", "TAZEC", 8),
	blocker="no reliable keyless testnet address provider is configured",
)

BUILTIN_RAILS = (
	bitcoin_testnet4,
	ethereum_sepolia,
	usdc_ethereum_sepolia,
	polygon_amoy,
	usdc_polygon_amoy,
	solana_devnet,
	usdc_solana_devnet,
	monero_stagenet,
	minotari_esmeralda,
	ootle_esmeralda,
	dash_testnet,
	zcash_testnet,
)


def builtin_rails():
	"""Return every built-in rail in stable display order."""
	return BUILTIN_RAILS
