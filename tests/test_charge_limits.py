"""Focused app-level tests for the database-backed charge ceilings.

The app normally runs inside Frappe.  These tests provide only the boundary
objects charge() uses, so the refusal can be proved without a live bench or a
network call.
"""

import importlib.util
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import ModuleType, SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 9, 4, 12, 0, 0)


class Refused(Exception):
	def __init__(self, message, title=None):
		super().__init__(message)
		self.title = title


class Record(SimpleNamespace):
	def get(self, key, default=None):
		return getattr(self, key, default)


class FakeDB:
	def __init__(self, records):
		self.records = list(records)
		self.settings_locked = False
		self.sale_reads = 0

	def sql(self, query, values=None, as_dict=False):
		if "`tabDocType`" in query:
			if "FOR UPDATE" not in query:
				raise AssertionError("charge admission mutex was not locked")
			self.settings_locked = True
			return [("CryptoPoS Settings",)]
		if "`tabCrypto Sale`" in query:
			if not self.settings_locked:
				raise AssertionError("sale counts were read before the database lock")
			if "FOR UPDATE" not in query:
				raise AssertionError("sale count was not a locking database read")
			self.sale_reads += 1
			open_states = values["open_states"]
			window_start = values["window_start"]
			return [
				record
				for record in self.records
				if record.state in open_states or record.creation >= window_start
			]
		raise AssertionError(f"unexpected SQL: {query}")

	def count(self, doctype, filters):
		if doctype != "Crypto Sale":
			raise AssertionError(f"unexpected count: {doctype}")
		return len(self.records)


class FakeSale:
	def __init__(self, frappe_module):
		self._frappe = frappe_module
		self.values = {}

	def update(self, values):
		self.values.update(values)
		for key, value in values.items():
			setattr(self, key, value)

	def insert(self, ignore_permissions=False):
		self._frappe.insert_calls += 1
		self.name = f"CPS-2026-{self._frappe.insert_calls:05d}"
		self._frappe.db.records.append(
			Record(state=self.state, creation=NOW, invoice_id=self.invoice_id)
		)

	def transition_to(self, state, source, detail):
		self._frappe.watcher_arms += 1
		self.state = state
		self._frappe.db.records[-1].state = state

	def save(self, ignore_permissions=False):
		self._frappe.save_calls += 1


class ChargeLimitTests(unittest.TestCase):
	EXPECTED_SALE_FIELDS = frozenset({
		"binding",
		"chain_reference",
		"charged_at",
		"credited_native",
		"identity_address",
		"identity_extras",
		"identity_source",
		"invoice_id",
		"invoice_ref",
		"invoiced_native",
		"loyalty_account",
		"loyalty_earn_rate",
		"merchant_name",
		"mode",
		"provenance",
		"qr_modules",
		"rail_key",
		"rate_at",
		"rate_lock_end",
		"rate_microcents",
		"rate_source",
		"sighted_native",
		"state",
		"uri",
		"usd_cents",
	})

	def setUp(self):
		self.saved_modules = {
			name: sys.modules.get(name)
			for name in (
				"frappe",
				"frappe.utils",
				"cryptopos.catalog",
				"cryptopos.rates",
				"cryptopos_core",
				"cryptopos_core.errors",
				"cryptopos_core.plugin",
				"cryptopos_core.qr",
				"cryptopos_core.rails",
			)
		}
		self.adapter = Record(
			key="btc",
			asset=Record(decimals=8),
			baseline_calls=0,
		)

		def validate_recipient(address):
			return None

		def capture_baseline(address, configuration):
			self.adapter.baseline_calls += 1
			return {"height": 100}

		def create_request(intent):
			return Record(uri="bitcoin:test?amount=0.000025", payer_notice="test only")

		self.adapter.validate_recipient = validate_recipient
		self.adapter.capture_baseline = capture_baseline
		self.adapter.create_request = create_request
		self.rail = Record(
			enabled=1,
			asset="BTC",
			unit_name="tBTC",
			label="Bitcoin testnet",
			name="btc",
			rail_key="btc",
			native_decimals=8,
			display_decimals=8,
			gate_text="1 confirmation",
			endpoint_for=lambda mode: "https://example.invalid",
			gate_for=lambda mode: 1,
			payment_component="",
		)
		self.settings = Record(
			mode="demo",
			merchant_name="Test Merchant",
			chain_reference=1,
			loyalty_earn_rate=0,
		)
		self._load_charge([])

	def tearDown(self):
		sys.modules.pop("charge_limits_under_test", None)
		for name, module in self.saved_modules.items():
			if module is None:
				sys.modules.pop(name, None)
			else:
				sys.modules[name] = module

	def _load_charge(self, records):
		frappe = ModuleType("frappe")
		frappe._ = lambda text: text
		frappe.db = FakeDB(records)
		frappe.insert_calls = 0
		frappe.save_calls = 0
		frappe.watcher_arms = 0
		frappe.throw = lambda message, title=None: (_ for _ in ()).throw(
			Refused(message, title)
		)
		frappe.get_single = lambda doctype: self.settings
		frappe.get_doc = lambda doctype, name: self.rail
		frappe.new_doc = lambda doctype: FakeSale(frappe)
		utils = ModuleType("frappe.utils")
		utils.add_to_date = lambda moment, seconds=0, **kwargs: moment + timedelta(
			seconds=seconds, **kwargs
		)
		utils.get_system_timezone = lambda: "UTC"
		utils.now_datetime = lambda: NOW
		frappe.utils = utils

		catalog = ModuleType("cryptopos.catalog")
		catalog.require_chargeable = lambda rail, mode: self.adapter
		catalog.configuration_for = lambda rail, mode: {"network": mode}
		catalog.recipient_calls = 0

		def recipient_for(rail, mode):
			catalog.recipient_calls += 1
			return "tb1qmerchant"

		catalog.recipient_for = recipient_for
		catalog.binding_label = lambda rail, mode: "shared"
		catalog.adapter_identity = lambda key: "tests.FakeAdapter"
		catalog.intent_to_record = lambda intent: vars(intent)

		rates = ModuleType("cryptopos.rates")
		rates.quote_calls = 0

		def quote(asset, mode):
			rates.quote_calls += 1
			return 2_000_000_000, "fixture", True

		rates.quote = quote

		core = ModuleType("cryptopos_core")
		qr = ModuleType("cryptopos_core.qr")
		qr.modules_for = lambda uri: [[True]]
		rails = ModuleType("cryptopos_core.rails")
		rails.invoice_amount = lambda scale, cents, rate: 2500
		errors = ModuleType("cryptopos_core.errors")

		class CryptoPosError(Exception):
			pass

		errors.CryptoPosError = CryptoPosError
		plugin = ModuleType("cryptopos_core.plugin")

		class PaymentIntent:
			def __init__(self, **values):
				self.__dict__.update(values)

		plugin.PaymentIntent = PaymentIntent
		core.qr = qr
		core.rails = rails

		sys.modules.update(
			{
				"frappe": frappe,
				"frappe.utils": utils,
				"cryptopos.catalog": catalog,
				"cryptopos.rates": rates,
				"cryptopos_core": core,
				"cryptopos_core.errors": errors,
				"cryptopos_core.plugin": plugin,
				"cryptopos_core.qr": qr,
				"cryptopos_core.rails": rails,
			}
		)
		spec = importlib.util.spec_from_file_location(
			"charge_limits_under_test", ROOT / "cryptopos" / "charge.py"
		)
		self.charge_module = importlib.util.module_from_spec(spec)
		sys.modules[spec.name] = self.charge_module
		spec.loader.exec_module(self.charge_module)
		self.frappe = frappe
		self.catalog = catalog
		self.rates = rates

	def _records(self, states, age_minutes=5):
		return [
			Record(state=state, creation=NOW - timedelta(minutes=age_minutes))
			for state in states
		]

	def test_charge_under_both_ceilings_is_unchanged(self):
		self.settings.max_open_sales = 2
		self.settings.max_sales_per_hour = 3
		self._load_charge(self._records(["awaiting"]))

		sale = self.charge_module.charge(5000, "btc", "loyalty-1")

		self.assertEqual(set(sale.values), self.EXPECTED_SALE_FIELDS)
		self.assertEqual(sale.state, "awaiting")
		self.assertEqual(sale.usd_cents, 5000)
		self.assertEqual(sale.mode, "demo")
		self.assertEqual(sale.loyalty_account, "loyalty-1")
		self.assertEqual(self.frappe.insert_calls, 1)
		self.assertEqual(self.frappe.watcher_arms, 1)
		self.assertEqual(self.catalog.recipient_calls, 1)
		self.assertEqual(self.adapter.baseline_calls, 1)

	def test_charge_that_would_cross_open_ceiling_is_refused_without_debris(self):
		self.settings.max_open_sales = 2
		self.settings.max_sales_per_hour = 20
		self._load_charge(self._records(["awaiting", "confirming"]))

		with self.assertRaisesRegex(Refused, "2 sales open at once") as refused:
			self.charge_module.charge(5000, "btc")

		self.assertEqual(refused.exception.title, "Open-sale limit reached")
		self.assertIn("Wait for an open sale to settle or expire", str(refused.exception))
		self.assertEqual(self.frappe.insert_calls, 0)
		self.assertEqual(self.frappe.watcher_arms, 0)
		self.assertEqual(self.catalog.recipient_calls, 0)
		self.assertEqual(self.adapter.baseline_calls, 0)
		self.assertEqual(self.rates.quote_calls, 0)

	def test_charge_that_would_cross_rolling_ceiling_is_refused_without_debris(self):
		self.settings.max_open_sales = 5
		self.settings.max_sales_per_hour = 3
		self._load_charge(self._records(["confirmed", "expired", "failed"]))

		with self.assertRaisesRegex(Refused, "3 sales in one hour") as refused:
			self.charge_module.charge(5000, "btc")

		self.assertEqual(refused.exception.title, "Hourly charge limit reached")
		self.assertIn("rolling one-hour window", str(refused.exception))
		self.assertEqual(self.frappe.insert_calls, 0)
		self.assertEqual(self.frappe.watcher_arms, 0)
		self.assertEqual(self.catalog.recipient_calls, 0)
		self.assertEqual(self.adapter.baseline_calls, 0)
		self.assertEqual(self.rates.quote_calls, 0)

	def test_legacy_settings_without_new_fields_use_conservative_defaults(self):
		self._load_charge(self._records(["awaiting"] * 5))

		with self.assertRaisesRegex(Refused, "5 sales open at once"):
			self.charge_module.charge(5000, "btc")

		self.assertEqual(self.frappe.insert_calls, 0)
		self.assertEqual(self.frappe.watcher_arms, 0)
		self.assertEqual(self.catalog.recipient_calls, 0)
		self.assertEqual(self.adapter.baseline_calls, 0)
		self.assertEqual(self.rates.quote_calls, 0)
		self.assertEqual(self.rates.quote_calls, 0)

	def test_legacy_settings_also_apply_default_rolling_ceiling(self):
		self._load_charge(self._records(["confirmed"] * 20))

		with self.assertRaisesRegex(Refused, "20 sales in one hour"):
			self.charge_module.charge(5000, "btc")

		self.assertEqual(self.frappe.insert_calls, 0)
		self.assertEqual(self.frappe.watcher_arms, 0)
		self.assertEqual(self.catalog.recipient_calls, 0)
		self.assertEqual(self.adapter.baseline_calls, 0)


if __name__ == "__main__":
	unittest.main()
