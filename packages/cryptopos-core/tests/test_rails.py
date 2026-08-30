"""Rails: the table is data, so the tests are about the table's shape.

Nothing here reaches a chain. The point of moving this table into the package
was that a rail's identity — its decimals, its family, what it promises — can
be checked in under a second, on any machine, with no bench under it. So the
assertions are the invariants every surface reads the table expecting, and
they fail on the day someone adds a rail that quietly breaks one.
"""

import unittest

from cryptopos_core import catalog, rails, rates
from cryptopos_core.errors import InvalidAmount, InvalidMode, InvalidRate

# Every rail must carry these. A surface reads whichever answers its own
# question, and a missing key is a KeyError at the counter rather than here.
REQUIRED = (
	"label",
	"chain",
	"asset",
	"family",
	"unit_name",
	"display_decimals",
	"native_decimals",
	"rate_cents",
	"gate_confs",
	"gate_text",
	"binding",
	"binding_category",
	"maturity",
	"maturity_note",
	"live_url",
	"testnet_url",
	"testnet_name",
	"real_transport",
	"real_block_time",
	"sim_block_seconds",
)

FAMILIES = {
	"bitcoin",
	"evm-native",
	"evm-erc20",
	"solana",
	"monero",
	"tari",
	"ootle",
	"zcash",
}

MATURITIES = {"works", "partial", "sim-always"}


class TableShape(unittest.TestCase):
	def test_carries_every_rail_that_came_across(self):
		# Pinned rather than counted loosely: this table is the whole reason
		# the module exists, and a rail silently dropped in an edit is the
		# failure that would otherwise show up as a missing button.
		self.assertEqual(
			rails.rail_keys(),
			(
				"btc",
				"eth",
				"usdc-eth",
				"pol",
				"usdc-pol",
				"sol",
				"usdc-sol",
				"xmr",
				"xtm",
				"xtr",
				"dash",
				"zec",
			),
		)

	def test_the_table_and_each_row_are_immutable(self):
		with self.assertRaises(TypeError):
			rails.RAILS["new"] = {}
		with self.assertRaises(TypeError):
			rails.RAILS["btc"]["native_decimals"] = 7

	def test_every_rail_carries_every_required_field(self):
		for key, rail in rails.RAILS.items():
			for field in REQUIRED:
				with self.subTest(rail=key, field=field):
					self.assertIn(field, rail)

	def test_every_family_is_one_a_watcher_exists_for(self):
		# The family selects the watcher implementation. An unknown one is a
		# rail nothing can answer for.
		for key, rail in rails.RAILS.items():
			with self.subTest(rail=key):
				self.assertIn(rail["family"], FAMILIES)

	def test_every_rail_states_a_maturity_in_the_vocabulary(self):
		for key, rail in rails.RAILS.items():
			with self.subTest(rail=key):
				self.assertIn(rail["maturity"], MATURITIES)

	def test_the_maturity_census_is_what_the_docs_claim(self):
		# README and DEVELOPMENT.md both state this split in prose. Prose
		# decays silently; this is the line that makes it not.
		census = {}
		for rail in rails.RAILS.values():
			census[rail["maturity"]] = census.get(rail["maturity"], 0) + 1
		self.assertEqual(census, {"works": 6, "partial": 3, "sim-always": 3})

	def test_every_rail_states_its_settle_gate_in_words(self):
		# A rail that settles without saying at what depth is a rail that
		# oversells. The ceiling ships on the surface offering the feature.
		for key, rail in rails.RAILS.items():
			with self.subTest(rail=key):
				self.assertTrue(rail["gate_text"].strip())

	def test_every_rail_states_how_a_payment_binds_to_a_sale(self):
		for key, rail in rails.RAILS.items():
			with self.subTest(rail=key):
				self.assertTrue(rail["binding"].strip())

	def test_a_balance_delta_is_never_an_unconditional_binding(self):
		"""D33: crediting a balance delta because a reference was seen is a race.

		`sol` may claim an unconditional per-sale binding because its adapter
		decodes the transfer instruction. A rail whose own prose says the
		amount comes from a balance delta has not earned that claim, and the
		claim is not inert: `declared_binding_category` hands a built-in rail's
		value to any installed plugin that declares none.
		"""
		for key, rail in rails.RAILS.items():
			prose = rail["binding"].lower()
			if "balance delta" in prose or "balance deltas" in prose:
				with self.subTest(rail=key):
					self.assertEqual(
						rail["binding_category"],
						"not-unconditional",
						f"{key} credits a balance delta yet claims an unconditional binding",
					)

	def test_only_a_rail_with_a_per_sale_identity_may_claim_one(self):
		"""The category is a claim about an adapter, and D33 is why.

		Solana Pay's reference was a sound protocol mechanism the whole time,
		and the rail still credited the wrong sale until the adapter decoded
		the transfer instruction. So a chain that *could* bind per sale has not
		bound anything. A rail may only claim an unconditional binding if this
		package actually gives each sale an identity of its own -- which is
		`catalog.REFERENCE_RAILS`. It matters because
		`declared_binding_category` lends a built-in claim to any plugin that
		declares none, and three operator-facing surfaces print it.
		"""
		for key, rail in rails.RAILS.items():
			if rail["binding_category"] == "unconditional-per-sale":
				with self.subTest(rail=key):
					self.assertIn(
						key,
						catalog.REFERENCE_RAILS,
						f"{key} claims a per-sale binding with no per-sale identity to bind to",
					)

	def test_binding_categories_state_what_holds_without_address_derivation(self):
		self.assertEqual(
			{key: rail["binding_category"] for key, rail in rails.RAILS.items()},
			{
				"btc": "not-unconditional",
				"eth": "not-unconditional",
				"usdc-eth": "not-unconditional",
				"pol": "not-unconditional",
				"usdc-pol": "not-unconditional",
				"sol": "unconditional-per-sale",
				"usdc-sol": "not-unconditional",
				"xmr": "not-unconditional",
				"xtm": "not-unconditional",
				"xtr": "not-unconditional",
				"dash": "not-unconditional",
				"zec": "not-unconditional",
			},
		)


class Decimals(unittest.TestCase):
	def test_native_is_never_coarser_than_display(self):
		# The unit math scales by 10**(native - display). A rail where that
		# is negative would not round badly, it would produce a fraction and
		# take a float into the money path.
		for key, rail in rails.RAILS.items():
			with self.subTest(rail=key):
				self.assertGreaterEqual(rail["native_decimals"], rail["display_decimals"])

	def test_decimals_are_non_negative_integers(self):
		for key, rail in rails.RAILS.items():
			with self.subTest(rail=key):
				for field in ("display_decimals", "native_decimals"):
					self.assertIsInstance(rail[field], int)
					self.assertGreaterEqual(rail[field], 0)


class PerNetworkIdentity(unittest.TestCase):
	"""A rail addressable on two networks must name both, or a testnet QR
	silently carries a mainnet contract and the money goes somewhere real."""

	def test_erc20_rails_name_a_contract_on_both_networks(self):
		for key, rail in rails.RAILS.items():
			if rail["family"] != "evm-erc20":
				continue
			with self.subTest(rail=key):
				self.assertTrue(rails.token_contract_for(rail, "mainnet"))
				self.assertTrue(rails.token_contract_for(rail, "testnet"))
				self.assertNotEqual(
					rails.token_contract_for(rail, "mainnet"),
					rails.token_contract_for(rail, "testnet"),
				)

	def test_evm_rails_name_a_chain_id_on_both_networks(self):
		for key, rail in rails.RAILS.items():
			if "chain_id" not in rail:
				continue
			with self.subTest(rail=key):
				self.assertIn("testnet_chain_id", rail)
				self.assertNotEqual(rail["chain_id"], rail["testnet_chain_id"])

	def test_token_rails_on_solana_name_a_mint_on_both_networks(self):
		for key, rail in rails.RAILS.items():
			if "token_mint" not in rail:
				continue
			with self.subTest(rail=key):
				self.assertIn("testnet_token_mint", rail)
				self.assertNotEqual(rail["token_mint"], rail["testnet_token_mint"])

	def test_mode_selects_the_contract(self):
		usdc = rails.RAILS["usdc-eth"]
		self.assertEqual(rails.token_contract_for(usdc, "testnet"), rails.USDC_ON_SEPOLIA)
		self.assertEqual(rails.token_contract_for(usdc, "mainnet"), rails.USDC_ON_ETHEREUM)
		self.assertEqual(rails.token_contract_for(usdc, "demo"), rails.USDC_ON_SEPOLIA)

	def test_mode_selects_the_mint(self):
		# The Solana half of the same rule. Asserted separately rather than
		# assumed from the contract case: they are two functions reading two
		# different pairs of keys, and only one of them was covered.
		usdc = rails.RAILS["usdc-sol"]
		self.assertEqual(rails.token_mint_for(usdc, "testnet"), rails.USDC_MINT_DEVNET)
		self.assertEqual(rails.token_mint_for(usdc, "mainnet"), rails.USDC_MINT_SOLANA)
		self.assertEqual(rails.token_mint_for(usdc, "demo"), rails.USDC_MINT_DEVNET)

	def test_only_explicit_mainnet_gets_the_real_deployment(self):
		usdc_eth, usdc_sol = rails.RAILS["usdc-eth"], rails.RAILS["usdc-sol"]
		for mode in ("demo", "testnet"):
			with self.subTest(mode=mode):
				self.assertEqual(rails.token_contract_for(usdc_eth, mode), rails.USDC_ON_SEPOLIA)
				self.assertEqual(rails.token_mint_for(usdc_sol, mode), rails.USDC_MINT_DEVNET)
		self.assertEqual(rails.token_contract_for(usdc_eth, "mainnet"), rails.USDC_ON_ETHEREUM)
		self.assertEqual(rails.token_mint_for(usdc_sol, "mainnet"), rails.USDC_MINT_SOLANA)

	def test_an_unknown_mode_is_refused_not_treated_as_mainnet(self):
		for select, rail in (
			(rails.token_contract_for, rails.RAILS["usdc-eth"]),
			(rails.token_mint_for, rails.RAILS["usdc-sol"]),
		):
			with self.subTest(select=select.__name__):
				with self.assertRaises(InvalidMode):
					select(rail, "maintnet")


class PriceAsset(unittest.TestCase):
	def test_a_rail_is_priced_in_its_own_asset_by_default(self):
		for key, rail in rails.RAILS.items():
			if "price_asset" in rail:
				continue
			with self.subTest(rail=key):
				self.assertEqual(rails.price_asset(rail), rail["asset"])

	def test_the_ootle_rail_is_priced_in_the_layer_one_asset(self):
		# XTR is Tari at layer two, XTM at layer one -- one asset, two
		# layers. An exchange lists the asset, not the layer, so an XTR row
		# in a feed table is a row that can never fill.
		self.assertEqual(rails.price_asset(rails.RAILS["xtr"]), "XTM")
		self.assertEqual(rails.RAILS["xtr"]["asset"], "XTR")

	def test_only_one_rail_needs_the_override(self):
		overrides = [key for key, rail in rails.RAILS.items() if "price_asset" in rail]
		self.assertEqual(overrides, ["xtr"])


class PolicyPoints(unittest.TestCase):
	def test_only_the_policy_layer_family_earns(self):
		# The money and the points have to be on the same network. This is a
		# restriction and it is meant to be one.
		for key, rail in rails.RAILS.items():
			with self.subTest(rail=key):
				self.assertEqual(
					rails.earns_policy_points(rail),
					rail["family"] == rails.POLICY_LAYER_FAMILY,
				)

	def test_the_restriction_currently_bites(self):
		# With the rule in force exactly one rail can offer points, and it is
		# the one with no exchange feed. That is the honest consequence of
		# the rule, not a defect in it -- and this test says so out loud.
		earning = [key for key, rail in rails.RAILS.items() if rails.earns_policy_points(rail)]
		self.assertEqual(earning, ["xtr"])


class UnitMath(unittest.TestCase):
	def test_the_worked_example_from_the_docstring(self):
		# $6.25 of ETH at $3,500: rounded ONCE at display precision (1785
		# micro-ETH), then scaled to wei.
		eth = rails.RAILS["eth"]
		rate = rails.rail_demo_microcents(eth)
		self.assertEqual(rate, 3_500 * rates.MICROCENTS_PER_USD)
		self.assertEqual(rails.usd_cents_to_native(eth, 625, rate), 1_785_000_000_000_000)

	def test_demo_rate_is_the_table_value_in_the_precise_unit(self):
		for key, rail in rails.RAILS.items():
			with self.subTest(rail=key):
				self.assertEqual(
					rails.rail_demo_microcents(rail),
					rail["rate_cents"] * rails.MICROCENTS_PER_CENT,
				)

	def test_the_scale_factor_is_derived_and_not_restated(self):
		self.assertEqual(rails.MICROCENTS_PER_CENT, 10_000)
		self.assertEqual(rails.MICROCENTS_PER_CENT * 100, rates.MICROCENTS_PER_USD)

	def test_omitting_a_rate_falls_back_to_the_demo_constant(self):
		# And nothing ever writes a live number back into RAILS: a
		# module-level dict that changes under the app is how a sale gets
		# priced at one rate and settled at another with no record of either.
		btc = rails.RAILS["btc"]
		self.assertEqual(
			rails.usd_cents_to_native(btc, 1099),
			rails.usd_cents_to_native(btc, 1099, rails.rail_demo_microcents(btc)),
		)

	def test_no_float_ever_appears(self):
		for key, rail in rails.RAILS.items():
			with self.subTest(rail=key):
				native = rails.usd_cents_to_native(rail, 1099)
				self.assertIsInstance(native, int)
				self.assertIsInstance(rails.native_to_usd_cents(rail, native), int)

	def test_inverse_conversion_refuses_invalid_inputs_consistently(self):
		btc = rails.RAILS["btc"]
		for rate in (0, -1, 1.9, True, "bad"):
			with self.subTest(rate=rate):
				with self.assertRaises(InvalidRate):
					rails.native_to_usd_cents(btc, 1, rate)
		for amount in (-1, 1.9, True, "bad"):
			with self.subTest(amount=amount):
				with self.assertRaises(InvalidAmount):
					rails.native_to_usd_cents(btc, amount, 1)
		self.assertEqual(rails.native_to_usd_cents(btc, 0, 1), 0)
		with self.assertRaises(InvalidAmount) as caught:
			rails.native_to_usd_cents(btc, -1, 1)
		self.assertEqual(caught.exception.minimum, 0)

	def test_forward_conversion_pins_zero_and_the_smallest_positive_rate(self):
		btc = rails.RAILS["btc"]
		with self.assertRaises(InvalidAmount):
			rails.usd_cents_to_native(btc, 0, 1)
		self.assertEqual(rails.usd_cents_to_native(btc, 1, 1), 10**12)

	def test_amount_helpers_never_truncate_nonintegers(self):
		btc = rails.RAILS["btc"]
		for helper in (rails.format_amount, rails.representable_amount, rails.is_exactly_displayable):
			for amount in (-1, 1.9, True, "bad"):
				with self.subTest(helper=helper.__name__, amount=amount):
					with self.assertRaises(InvalidAmount):
						helper(btc, amount)

	def test_amount_helpers_accept_zero_and_report_a_zero_minimum(self):
		btc = rails.RAILS["btc"]
		self.assertEqual(rails.format_amount(btc, 0), "0.00000000")
		self.assertEqual(rails.representable_amount(btc, 0), 0)
		self.assertTrue(rails.is_exactly_displayable(btc, 0))
		for helper in (rails.format_amount, rails.representable_amount, rails.is_exactly_displayable):
			with self.subTest(helper=helper.__name__):
				with self.assertRaises(InvalidAmount) as caught:
					helper(btc, -1)
				self.assertEqual(caught.exception.minimum, 0)

	def test_the_inverse_rounds_the_same_way_as_the_forward(self):
		# They must floor identically at the same precision. If they did not,
		# a sale that paid exactly would read as short by more than the one
		# cent flooring costs.
		for key, rail in rails.RAILS.items():
			with self.subTest(rail=key):
				native = rails.usd_cents_to_native(rail, 1099)
				back = rails.native_to_usd_cents(rail, native)
				self.assertLessEqual(back, 1099)
				self.assertGreaterEqual(back, 1099 - 1)

	def test_small_amounts_do_not_round_to_zero(self):
		# The reason for rounding at display precision rather than native:
		# one cent of ETH is 2 micro-ETH, and it must survive the scaling.
		eth = rails.RAILS["eth"]
		self.assertGreater(rails.usd_cents_to_native(eth, 1), 0)

	def test_format_amount_is_the_display_form(self):
		eth = rails.RAILS["eth"]
		self.assertEqual(rails.format_amount(eth, 1_785_000_000_000_000), "0.001785")
		btc = rails.RAILS["btc"]
		self.assertEqual(rails.format_amount(btc, 195_300), "0.00195300")

	def test_format_amount_pads_to_display_decimals(self):
		# A payment URI amount that lost its trailing zeros is still correct
		# arithmetic and a different string, and the string is what is signed.
		for key, rail in rails.RAILS.items():
			with self.subTest(rail=key):
				text = rails.format_amount(rail, rails.usd_cents_to_native(rail, 1099))
				self.assertEqual(len(text.split(".")[1]), rail["display_decimals"])


class TwoConversionsThatDisagree(unittest.TestCase):
	"""`rails.usd_cents_to_native` and `rates.native_for` answer the same
	question differently, and both are now in this package.

	`rates.native_for` divides straight to native precision; the rail path
	rounds once at DISPLAY precision and scales up. On ETH that is a
	difference of 714285714285 wei on a $6.25 sale -- not a rounding wobble,
	a deliberate truncation to the precision a human is shown.

	This is pinned rather than reconciled because reconciling it changes what
	a live terminal invoices: the Frappe app charges through `native_for`,
	the tkinter terminal charges through `usd_cents_to_native`, and picking
	one is a money decision rather than a tidy-up.
	"""

	def test_the_two_paths_differ_on_a_rail_with_room_between_the_decimals(self):
		eth = rails.RAILS["eth"]
		rate = rails.rail_demo_microcents(eth)
		display_path = rails.usd_cents_to_native(eth, 625, rate)
		native_path = rates.native_for(625, rate, eth["native_decimals"])
		self.assertEqual(display_path, 1_785_000_000_000_000)
		self.assertEqual(native_path, 1_785_714_285_714_285)
		self.assertLess(display_path, native_path)

	def test_they_agree_when_display_precision_is_native_precision(self):
		# On BTC, USDC and every other rail whose two decimals match, there
		# is nothing to truncate and the two paths are the same arithmetic.
		for key, rail in rails.RAILS.items():
			if rail["display_decimals"] != rail["native_decimals"]:
				continue
			with self.subTest(rail=key):
				rate = rails.rail_demo_microcents(rail)
				self.assertEqual(
					rails.usd_cents_to_native(rail, 1099, rate),
					rates.native_for(1099, rate, rail["native_decimals"]),
				)


class Lookup(unittest.TestCase):
	def test_rail_for_returns_the_table_entry(self):
		self.assertIs(rails.rail_for("btc"), rails.RAILS["btc"])

	def test_an_unknown_rail_raises_rather_than_returning_none(self):
		# A None here becomes a sale charged against nothing.
		with self.assertRaises(KeyError):
			rails.rail_for("nosuchrail")


if __name__ == "__main__":
	unittest.main()


class ContractAddresses(unittest.TestCase):
	"""Every EVM address in the table is EIP-55 checksummed.

	`USDC_ON_POLYGON` was not, when the table arrived: a lowercase `c` where
	the checksum wants `C`. The bytes were right, so a transfer would have
	worked, and that is exactly what makes it worth pinning — a
	hand-transcribed address nothing verified is how the same slip reaches a
	RECIPIENT address, where it is not recoverable.
	"""

	def test_every_token_contract_is_eip55_valid(self):
		from cryptopos_core.addresses import OK, validate

		for name in (
			"USDC_ON_ETHEREUM",
			"USDC_ON_POLYGON",
			"USDC_ON_SEPOLIA",
			"USDC_ON_AMOY",
		):
			with self.subTest(constant=name):
				verdict, reason = validate("eth", getattr(rails, name), "mainnet")
				self.assertEqual(verdict, OK, f"{name}: {reason}")

	def test_the_contracts_named_by_rails_are_the_ones_checked(self):
		# Guards against a rail pointing at some other address entirely.
		from cryptopos_core.addresses import OK, validate

		for key, rail in rails.RAILS.items():
			for field in ("token_contract", "testnet_token_contract"):
				if field not in rail:
					continue
				with self.subTest(rail=key, field=field):
					self.assertEqual(validate("eth", rail[field], "mainnet")[0], OK)


class Representability(unittest.TestCase):
	"""A decimal URI carries the DISPLAY form. If the display form is a
	truncation of the invoice, the customer pays what they were shown and the
	sale sits short of itself forever."""

	def test_invoice_amount_is_exactly_displayable_on_every_rail(self):
		for key, rail in rails.RAILS.items():
			for cents in (1, 99, 1099, 250_000):
				with self.subTest(rail=key, cents=cents):
					amount = rails.invoice_amount(rail, cents, rails.rail_demo_microcents(rail))
					self.assertTrue(rails.is_exactly_displayable(rail, amount))

	def test_native_for_is_not_always_exactly_displayable(self):
		# The reason `invoice_amount` exists. On SOL a $10.99 sale priced
		# through the primitive lands 666 lamports off the display grid.
		sol = rails.RAILS["sol"]
		amount = rates.native_for(1099, rails.rail_demo_microcents(sol), sol["native_decimals"])
		self.assertFalse(rails.is_exactly_displayable(sol, amount))
		self.assertEqual(rails.representable_amount(sol, amount), 73_266_000)

	def test_representable_rounds_down_never_up(self):
		# Up would invoice for native units the cent amount does not cover.
		for key, rail in rails.RAILS.items():
			with self.subTest(rail=key):
				for amount in (1, 12_345_678_901, 999_999_999_999_999):
					self.assertLessEqual(rails.representable_amount(rail, amount), amount)

	def test_rails_whose_decimals_match_can_state_anything(self):
		for key, rail in rails.RAILS.items():
			if rail["display_decimals"] != rail["native_decimals"]:
				continue
			with self.subTest(rail=key):
				self.assertTrue(rails.is_exactly_displayable(rail, 12_345_678_901))

	def test_format_amount_round_trips_a_representable_amount(self):
		# The invariant stated directly: what the URI says, parsed back, is
		# what the sale invoiced.
		from decimal import Decimal

		for key, rail in rails.RAILS.items():
			with self.subTest(rail=key):
				amount = rails.invoice_amount(rail, 1099, rails.rail_demo_microcents(rail))
				text = rails.format_amount(rail, amount)
				parsed = int(Decimal(text) * (10 ** rail["native_decimals"]))
				self.assertEqual(parsed, amount)


class ChargePathRequiresAnExplicitRate(unittest.TestCase):
	"""`usd_cents_to_native` may fall back to the demo constant.
	`invoice_amount` may not, and that is the whole difference between them.

	A charge path that reached the demo fallback by forgetting an argument
	would price real money at a number nobody quoted — the same hazard
	`rates.quote` refuses on mainnet, arriving through a different door.
	"""

	def test_omitting_the_rate_is_an_error_at_the_call_site(self):
		with self.assertRaises(TypeError):
			rails.invoice_amount(rails.RAILS["btc"], 1099)

	def test_passing_none_is_refused_rather_than_defaulted(self):
		with self.assertRaises(InvalidRate):
			rails.invoice_amount(rails.RAILS["btc"], 1099, None)

	def test_nonpositive_and_malformed_rates_are_refused_consistently(self):
		for value in (0, -1, "not-a-rate", 1.9, True):
			with self.subTest(value=value):
				with self.assertRaises(InvalidRate):
					rails.invoice_amount(rails.RAILS["btc"], 1099, value)

	def test_nonpositive_sales_are_not_invoices(self):
		for value in (0, -1, 1.9, True, None):
			with self.subTest(value=value):
				with self.assertRaises(InvalidAmount):
					rails.invoice_amount(rails.RAILS["btc"], value, 1_000_000)

	def test_an_amount_that_rounds_to_zero_is_refused(self):
		with self.assertRaises(InvalidAmount):
			rails.invoice_amount(rails.RAILS["btc"], 1, 10**20)

	def test_the_smallest_nonzero_invoice_is_accepted(self):
		unit_rail = {"display_decimals": 0, "native_decimals": 0}
		self.assertEqual(rails.invoice_amount(unit_rail, 1, 10_000), 1)

	def test_the_demo_rate_still_works_when_asked_for_by_name(self):
		# Pricing from the demo table stays possible and becomes visible in
		# the call, which is the point.
		btc = rails.RAILS["btc"]
		self.assertEqual(
			rails.invoice_amount(btc, 1099, rails.rail_demo_microcents(btc)),
			rails.usd_cents_to_native(btc, 1099),
		)

	def test_the_lower_level_helper_keeps_its_fallback(self):
		# Unchanged for the tkinter terminal, which relies on it.
		btc = rails.RAILS["btc"]
		self.assertEqual(rails.usd_cents_to_native(btc, 1099), 17_171)


# ---------------------------------------------------------------------------
# The table's VALUES, not its shape.
#
# `TableShape` asserts that every rail carries the right keys with the right
# types, and every one of the numbers below could be wrong by one without it
# noticing -- which is exactly what mutation testing found: 48 separate edits
# to this table left the whole suite green.
#
# The columns are not equally serious, and they are pinned for different
# reasons:
#
#   native_decimals    the worst of them. Off by one is a 10x error in the
#                      amount invoiced, in a rail's own units, silently.
#   display_decimals   what the URI states and the customer's wallet reads.
#                      Off by one asks for the wrong number.
#   gate_confs         when a sale is called settled. Too low hands over goods
#                      before the chain has agreed.
#   chain_id           which network an EVM URI names. Wrong means a wallet
#                      offering to pay on a chain nobody is watching.
#   rate_cents         demo only, and still pinned: a demo rate that drifts
#                      makes every demo assertion about amounts drift with it.
#   sim_block_seconds  simulator pacing, the least serious, pinned because it
#                      costs one line to stop it moving by accident.
#
# A rail added without a row here fails `test_the_golden_table_covers_every_rail`.
# ---------------------------------------------------------------------------

# key: (display_decimals, native_decimals, gate_confs, rate_cents, sim_block_seconds)
GOLDEN = {
	"btc": (8, 8, 3, 6_400_000, 15),
	"eth": (6, 18, 3, 350_000, 10),
	"usdc-eth": (6, 6, 3, 100, 10),
	"pol": (6, 18, None, 55, 4),
	"usdc-pol": (6, 6, None, 100, 4),
	"sol": (6, 9, None, 15_000, 2),
	"usdc-sol": (6, 6, None, 100, 2),
	"xmr": (6, 12, 10, 16_500, 12),
	"xtm": (2, 6, None, 2, 12),
	"xtr": (2, 6, None, 5, 10),
	"dash": (8, 8, 6, 2_500, 10),
	"zec": (8, 8, 10, 4_000, 12),
}

# Only Bitcoin settles at a different depth on testnet: 3 mainnet, 1 testnet.
GOLDEN_TESTNET_GATES = {"btc": 1}

# EIP-155 chain IDs. Ethereum 1 / Sepolia 11155111, Polygon 137 / Amoy 80002.
GOLDEN_CHAIN_IDS = {
	"eth": (1, 11155111),
	"usdc-eth": (1, 11155111),
	"pol": (137, 80002),
	"usdc-pol": (137, 80002),
}


class GoldenTable(unittest.TestCase):
	def test_the_golden_table_covers_every_rail(self):
		# A new rail must be priced and gated deliberately, not inherit
		# whatever a copied row happened to say.
		self.assertEqual(set(GOLDEN), set(rails.RAILS))

	def test_decimals(self):
		for key, (display, native, _gate, _rate, _sim) in GOLDEN.items():
			with self.subTest(rail=key):
				self.assertEqual(rails.RAILS[key]["display_decimals"], display)
				self.assertEqual(rails.RAILS[key]["native_decimals"], native)

	def test_display_precision_never_exceeds_native_precision(self):
		# A display that is finer than the chain's own unit would state an
		# amount the chain cannot represent.
		for key, rail in rails.RAILS.items():
			with self.subTest(rail=key):
				self.assertLessEqual(rail["display_decimals"], rail["native_decimals"])

	def test_settle_gates(self):
		for key, (_display, _native, gate, _rate, _sim) in GOLDEN.items():
			with self.subTest(rail=key):
				self.assertEqual(rails.RAILS[key]["gate_confs"], gate)

	def test_testnet_gates(self):
		for key, rail in rails.RAILS.items():
			with self.subTest(rail=key):
				self.assertEqual(rail.get("testnet_gate_confs"), GOLDEN_TESTNET_GATES.get(key))

	def test_demo_rates(self):
		for key, (_display, _native, _gate, rate, _sim) in GOLDEN.items():
			with self.subTest(rail=key):
				self.assertEqual(rails.RAILS[key]["rate_cents"], rate)

	def test_simulator_pacing(self):
		for key, (_display, _native, _gate, _rate, sim) in GOLDEN.items():
			with self.subTest(rail=key):
				self.assertEqual(rails.RAILS[key]["sim_block_seconds"], sim)

	def test_chain_ids(self):
		for key, (mainnet, testnet) in GOLDEN_CHAIN_IDS.items():
			with self.subTest(rail=key):
				self.assertEqual(rails.RAILS[key]["chain_id"], mainnet)
				self.assertEqual(rails.RAILS[key]["testnet_chain_id"], testnet)

	def test_only_evm_rails_carry_a_chain_id(self):
		carriers = {key for key, rail in rails.RAILS.items() if "chain_id" in rail}
		self.assertEqual(carriers, set(GOLDEN_CHAIN_IDS))

	def test_the_two_rails_that_carry_a_flag_of_their_own(self):
		# Dash inherits Bitcoin's watcher shape PLUS ChainLocks, and Ootle is
		# the one rail with no simulator personality -- demo is refused with a
		# stated reason rather than answered by fiction. Both are single
		# booleans that change behaviour and nothing else asserted them.
		self.assertIs(rails.RAILS["dash"]["dash_chainlocks"], True)
		self.assertIs(rails.RAILS["xtr"]["no_simulator"], True)

		self.assertEqual({key for key, rail in rails.RAILS.items() if rail.get("dash_chainlocks")}, {"dash"})
		self.assertEqual({key for key, rail in rails.RAILS.items() if rail.get("no_simulator")}, {"xtr"})
