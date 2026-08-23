"""Payment URIs — one branch per scheme, and the QR encodes exactly these.

Carried across from the tkinter terminal unchanged. The scheme, the amount
form and the network marker are each a decision some wallet enforces, so they
are written once here rather than reconstructed at whatever surface happens to
be drawing a code.

`mode` is the sale's CHARGE-TIME mode, not whatever the terminal's switch says
later: the captured mode drives the QR's chain id and address flavour, so a
testnet QR is chosen HERE and cannot silently become a mainnet one when an
operator flips a setting mid-sale.

Two amount forms appear below and the split is not cosmetic. BIP-21, Solana
Pay and ZIP-321 carry a DECIMAL amount; ERC-681 and Tari's RFC-0154 deeplink
carry the INTEGER native amount. Sending the wrong one is not a rounding
difference -- it is off by 10^18.

**This function is the money boundary, so it refuses rather than guesses.**
Two checks run before any string is built, and both raise:

    the address     checksum-verified and bound to the sale's network, via
                    `addresses.validate`. On mainnet an address this build
                    cannot verify is refused too -- see below.
    the amount      must be statable EXACTLY in the scheme's amount form.

The amount check exists because of a defect it is worth naming. A
decimal-amount URI carries `format_amount`, which truncates to display
precision. Invoice a SOL sale through `rates.native_for` and the QR asks for
73266000 lamports against an invoice of 73266666 -- the customer pays exactly
what they were shown, the sale sits 666 short of itself, and no amount of
waiting resolves it. That is an unresolvable review over real money, so it is
refused at the point the URI would have been built.

**`strict` defaults to True and mainnet ignores attempts to turn it off.**
An unverifiable address is tolerable on testnet, where the money is not real
and a `sim-always` rail may be the whole point. On mainnet it is not, and a
flag that lets a caller opt out of the last check before real funds move is
a flag that will eventually be passed by accident.
"""

import hashlib

from .addresses import OK, REFUSED, validate
from .errors import (
	AddressRefused,
	AmountNotRepresentable,
	InvalidAmount,
	InvalidPaymentIdentity,
	UnsupportedRail,
	_coerce_integer,
)
from .modes import MAINNET, require_mode
from .rails import (
	RAILS,
	format_amount,
	is_exactly_displayable,
	representable_amount,
	token_contract_for,
	token_mint_for,
)

# Schemes whose amount rides as a decimal display string. Only these can
# truncate, so only these need the representability check.
DECIMAL_AMOUNT_RAILS = frozenset({"btc", "dash", "zec", "sol", "usdc-sol", "xmr"})


def build_uri(rail_key, identity, native_units, mode, strict=True):
	"""Build the payment URI for `rail_key`. See the module docstring.

	Raises `AddressRefused` or `AmountNotRepresentable` -- both
	`CryptoPosError` -- rather than returning a URI it cannot stand behind.
	"""
	# `mode` is the sale's CHARGE-TIME mode: your Mode Safety note says
	# the captured mode drives "QR chain id and address flavor", so the
	# testnet chain ids / contracts / authorities are chosen HERE, not
	# by whatever the terminal's switch says later. Mainnet identities are
	# selected only by explicit mainnet; demo cannot emit an executable
	# mainnet payment instruction.
	require_mode(mode)
	try:
		rail = RAILS[rail_key]
	except (KeyError, TypeError):
		raise UnsupportedRail(rail_key) from None
	try:
		address = identity["address"]
	except (KeyError, TypeError):
		raise InvalidPaymentIdentity(rail_key, "address", None, "no address was supplied") from None
	if not isinstance(address, str):
		raise InvalidPaymentIdentity(rail_key, "address", address, "the address must be text")
	address = address.strip()
	if not address or any(character.isspace() or character in "?&#%" for character in address):
		raise InvalidPaymentIdentity(
			rail_key,
			"address",
			address,
			"the address is empty or contains characters that can change URI structure",
		)

	normalized_units = _coerce_integer(native_units)
	if normalized_units is None:
		raise InvalidAmount("native_units", native_units) from None
	native_units = normalized_units
	if native_units <= 0:
		raise InvalidAmount("native_units", native_units)

	# Mainnet is never lenient, whatever the caller asked for.
	if strict or mode == MAINNET:
		verdict, reason = validate(rail_key, address, mode)
		if verdict == REFUSED:
			raise AddressRefused(rail_key, address, verdict, reason)
		if verdict != OK and mode == MAINNET:
			raise AddressRefused(
				rail_key,
				address,
				verdict,
				f"{reason}, and this is a mainnet sale -- an address that cannot be "
				f"checked is not one to send real money to",
			)

	# Amount exactness is independent of address strictness. `strict=False`
	# exists for unverifiable test/demo addresses; it must never turn a payment
	# for less than the invoice back on.
	if rail_key in DECIMAL_AMOUNT_RAILS and not is_exactly_displayable(rail, native_units):
		raise AmountNotRepresentable(rail_key, native_units, representable_amount(rail, native_units))

	amount = format_amount(rail, native_units)  # decimal display form

	if rail_key == "btc":
		# BIP-21: decimal BTC, 8dp, period separator, no commas
		return f"bitcoin:{address}?amount={amount}"
	if rail_key in ("eth", "pol"):
		# ERC-681 native: amount in WEI, integer; chain id per mode
		# (vault: mainnet @1/@137, Sepolia @11155111, Amoy @80002)
		chain_id = rail["chain_id"] if mode == MAINNET else rail["testnet_chain_id"]
		return f"ethereum:{address}@{chain_id}?value={native_units}"
	if rail_key in ("usdc-eth", "usdc-pol"):
		# ERC-681 token transfer: the URI TARGETS THE TOKEN CONTRACT,
		# and the merchant is a parameter. atomic micro-USDC integer.
		chain_id = rail["chain_id"] if mode == MAINNET else rail["testnet_chain_id"]
		return (
			f"ethereum:{token_contract_for(rail, mode)}@{chain_id}"
			f"/transfer?address={address}&uint256={native_units}"
		)
	if rail_key == "sol":
		# Solana Pay: decimal amount; the reference key is the binding.
		# (No cluster in the URI - the wallet's own setting decides.)
		reference = _solana_reference(identity, rail_key)
		return f"solana:{address}?amount={amount}&reference={reference}"
	if rail_key == "usdc-sol":
		reference = _solana_reference(identity, rail_key)
		return (
			f"solana:{address}?amount={amount}&spl-token={token_mint_for(rail, mode)}&reference={reference}"
		)
	if rail_key == "xmr":
		# opaque form only - "monero://" is rejected by some wallets
		return f"monero:{address}?tx_amount={amount}"
	if rail_key == "xtm":
		# RFC-0154 deeplink; amount is INTEGER MicroTari, no decimal
		# point. The wallet ENFORCES the network authority (vault Tari
		# note: "mainnet" or "esmeralda") - a testnet QR literally
		# cannot be paid with mainnet coin.
		authority = "mainnet" if mode == MAINNET else "esmeralda"
		return f"tari://{authority}/transactions/send?tariAddress={address}&amount={native_units}"
	if rail_key == "dash":
		# BIP-21 style; NEVER emit req-IS=1 (voids the URI). Locking is
		# automatic on Dash - nothing to ask for.
		return f"dash:{address}?amount={amount}"
	if rail_key == "zec":
		# ZIP-321 forbids a memo on a transparent recipient. This package
		# intentionally supports transparent addresses only, so the fresh
		# per-sale address is the binding and no memo may appear here.
		return f"zcash:{address}?amount={amount}"
	raise UnsupportedRail(rail_key)


# ===========================================================================
# Base58 - Solana addresses/keys are 32 bytes base58-encoded. ~10 lines,
# so we do it ourselves rather than add a dependency. (Same alphabet
# Bitcoin invented: no 0/O/I/l, so addresses survive being read aloud.)
# ===========================================================================

_B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_B58_INDEX = {character: value for value, character in enumerate(_B58_ALPHABET)}


def _base58_decode(text):
	number = 0
	for character in text:
		value = _B58_INDEX.get(character)
		if value is None:
			return None
		number = number * 58 + value
	body = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
	leading = len(text) - len(text.lstrip("1"))
	return b"\x00" * leading + body


def _solana_reference(identity, rail_key):
	try:
		reference = identity["reference"]
	except (KeyError, TypeError):
		raise InvalidPaymentIdentity(
			rail_key, "reference", None, "no sale-binding reference was supplied"
		) from None
	if not isinstance(reference, str):
		raise InvalidPaymentIdentity(rail_key, "reference", reference, "the reference must be text")
	raw = _base58_decode(reference)
	if raw is None or len(raw) != 32:
		raise InvalidPaymentIdentity(
			rail_key,
			"reference",
			reference,
			"a Solana reference must be a base58-encoded 32-byte public key",
		)
	return reference


def base58_encode(raw_bytes):
	number = int.from_bytes(raw_bytes, "big")
	text = ""
	while number:
		number, digit = divmod(number, 58)
		text = _B58_ALPHABET[digit] + text
	# leading zero bytes encode as leading '1's
	return "1" * (len(raw_bytes) - len(raw_bytes.lstrip(b"\0"))) + text


def fresh_32_bytes(seed_text):
	"""A deterministic-per-sale 32-byte value (for Solana reference keys)."""
	return hashlib.sha256(seed_text.encode()).digest()
