"""Reading the Ootle policy tier — free, keyless, feeless.

Every function here is TOTAL. Nothing raises. A read that fails returns a
sentinel and a reason, because the rule above this module is absolute:

    a sale must never fail because the policy layer is down.

Reading needs no account and costs nothing, and that is the property the
whole verification story rests on — a merchant's promise is checkable by the
customer who holds the card, from any machine, at no cost. A limit that costs
money to check is a limit most people will not check.

The component's state is a positional CBOR array. The mapping below was
verified against the deployed K1 component by matching all four resource
slots to ootle-testnet/ADDRESSES.md; a shape that does not match is a
REFUSAL, not a guess. A wrong rate on a screen is worse than no rate at all,
because it will be believed.
"""

import json
import urllib.error
import urllib.request

import frappe

# Short on purpose. A screen may be building in front of a waiting customer,
# so a slow indexer must degrade to "unavailable" rather than hang a render.
READ_TIMEOUT_SECONDS = 4.0

USER_AGENT = "cryptopos/0.0.1"

# Positional layout of Loyalty's component state, confirmed on-chain.
SLOT_POINTS_RESOURCE = 5
SLOT_ENTITLEMENT_RESOURCE = 6
SLOT_ENROLMENT_RESOURCE = 7
SLOT_VAULT_CLAIM_RESOURCE = 8
SLOT_REDEMPTION_RATE = 9
SLOT_PER_ISSUE_CEILING = 10
SLOT_PER_EPOCH_CEILING = 11
SLOT_WINDOW_EPOCH = 12
SLOT_COMMITTED_THIS_EPOCH = 13
EXPECTED_MIN_SLOTS = 15


def _settings():
	return frappe.get_cached_doc("CryptoPoS Settings")


def indexer():
	return (_settings().ootle_indexer or "https://ootle-indexer-a.tari.com").rstrip("/")


def _get(path):
	"""GET {indexer}/{path}. Returns (body, None) or (None, reason)."""
	url = f"{indexer()}/{path}"
	try:
		request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
		with urllib.request.urlopen(request, timeout=READ_TIMEOUT_SECONDS) as response:
			return json.loads(response.read().decode("utf-8")), None
	except urllib.error.HTTPError as exception:
		return None, f"the indexer answered {exception.code} for {path}"
	except (urllib.error.URLError, OSError) as exception:
		return None, f"the indexer did not answer: {exception}"
	except ValueError as exception:
		return None, f"the indexer answered something that is not JSON: {exception}"


def available():
	"""Is the policy layer reachable at all? Never raises."""
	body, reason = _get("network")
	if body is None:
		return False, reason
	return True, body.get("network", "")


def _hex_of(entry):
	if isinstance(entry, dict) and isinstance(entry.get("value"), dict):
		return entry["value"].get("hex")
	return None


def _amount(entry):
	# Amounts arrive as a two-element array; the first element is the value.
	if isinstance(entry, list) and entry:
		return int(entry[0])
	if isinstance(entry, int):
		return int(entry)
	return None


def promise():
	"""Read the deployed contract's own account of itself.

	Returns (facts, None) or (None, reason). `facts` carries the rate and
	both ceilings — the numbers that must appear on any surface offering
	the feature.
	"""
	settings = _settings()
	component = (settings.loyalty_component or "").strip()
	if not component:
		return None, "no loyalty component is configured"

	body, reason = _get(f"substates/{component}")
	if body is None:
		return None, reason

	try:
		state = body["substate"]["Component"]["body"]["state"]
		header = body["substate"]["Component"]["header"]
	except (KeyError, TypeError):
		return None, "the component answered in a shape this build does not recognise"

	if not isinstance(state, list) or len(state) < EXPECTED_MIN_SLOTS:
		return None, "the component answered in a shape this build does not recognise"

	rate = state[SLOT_REDEMPTION_RATE]
	per_issue = _amount(state[SLOT_PER_ISSUE_CEILING])
	per_epoch = _amount(state[SLOT_PER_EPOCH_CEILING])
	points_resource = _hex_of(state[SLOT_POINTS_RESOURCE])

	if not isinstance(rate, int) or per_issue is None or per_epoch is None or not points_resource:
		return None, "the component answered in a shape this build does not recognise"

	# The configured resource must be the one the component actually names.
	# A mismatch means the settings point at a superseded component -- the
	# old addresses still resolve and still answer, which is exactly how a
	# stale address gets believed.
	configured = (settings.loyalty_points_resource or "").strip()
	named = f"resource_{points_resource}"
	if configured and configured != named:
		return None, (
			f"the configured points resource is not the one this component names "
			f"({configured} vs {named}); refusing rather than reading the wrong ledger"
		)

	return (
		{
			"component": component,
			"version": body.get("version"),
			"owner_rule": header.get("owner_rule"),
			"redemption_rate": rate,
			"per_issue_ceiling": per_issue,
			"per_epoch_ceiling": per_epoch,
			"window_epoch": state[SLOT_WINDOW_EPOCH],
			"committed_this_epoch": _amount(state[SLOT_COMMITTED_THIS_EPOCH]),
			"points_resource": named,
			"entitlement_resource": f"resource_{_hex_of(state[SLOT_ENTITLEMENT_RESOURCE])}",
		},
		None,
	)


def points_balance(account, points_resource):
	"""Read a customer's points balance. Returns (points, None) or (None, reason).

	A balance of 0 and an unreadable balance are different answers and are
	returned differently. NEVER call this on the path of a sale.
	"""
	if not account:
		return None, "no account given"

	body, reason = _get(f"substates/{account}")
	if body is None:
		return None, reason

	try:
		vaults = body["substate"]["Component"]["body"]["state"]
	except (KeyError, TypeError):
		return None, "the account answered in a shape this build does not recognise"

	# Walk the account's vaults for one holding this resource. An account
	# with no such vault is a customer who has never been awarded -- that is
	# zero, and it is not an error.
	wanted = points_resource.replace("resource_", "")
	found = _walk_for_resource(vaults, wanted)
	if found is None:
		return 0, None

	vault_body, reason = _get(f"substates/{found}")
	if vault_body is None:
		return None, reason
	return _balance_of(vault_body)


def _walk_for_resource(node, wanted, depth=0):
	"""Find a vault id keyed against `wanted` in an account's state tree."""
	if depth > 6:
		return None
	if isinstance(node, dict):
		hexed = _hex_of(node)
		if hexed and hexed == wanted:
			return None  # this is the resource itself, not its vault
		for value in node.values():
			found = _walk_for_resource(value, wanted, depth + 1)
			if found:
				return found
	if isinstance(node, list):
		# Accounts store vaults as (resource, vault) pairs in a map; when the
		# resource matches, the sibling entry is the vault we want.
		hexes = [_hex_of(item) for item in node if isinstance(item, dict)]
		if wanted in [h for h in hexes if h]:
			for item in node:
				hexed = _hex_of(item)
				if hexed and hexed != wanted:
					return f"vault_{hexed}"
		for item in node:
			found = _walk_for_resource(item, wanted, depth + 1)
			if found:
				return found
	return None


def _balance_of(vault_body):
	try:
		vault = vault_body["substate"]["Vault"]
		fungible = vault["resource_container"]["Fungible"]
		return int(_amount(fungible["amount"]) or 0), None
	except (KeyError, TypeError, ValueError):
		return None, "the vault answered in a shape this build does not recognise"


# ---------------------------------------------------------------------------
# The ceilings, in words. Charter §2 rule 3: a ceiling ships on the surface
# that offers the feature, beside the promise it bounds -- not in
# documentation to be discovered later.
# ---------------------------------------------------------------------------
def ceilings_wording(facts):
	"""Every limit that must be displayed wherever loyalty is offered."""
	rate = facts["redemption_rate"]
	return [
		(
			"The rate is locked. Prices are not.",
			f"{rate} points buy one cent, and that rate can never change — no "
			f"method on this component writes it and none can be added. This "
			f"promises we will not change the rate, never that your points keep "
			f"their value: the merchant still sets prices.",
		),
		(
			"Points cannot be sold or transferred.",
			"They are earnable and redeemable here, and nowhere else. Nothing "
			"can move them off your account.",
		),
		(
			"Ceilings tighten only.",
			f"{facts['per_issue_ceiling']:,} points per award and "
			f"{facts['per_epoch_ceiling']:,} per epoch. Both can be lowered and "
			f"neither can ever be raised.",
		),
		(
			"A redemption must be whole cents' worth.",
			"The remainder stays yours rather than being rounded away.",
		),
		(
			"What earning publishes.",
			"Points land on a public network. The account is linkable and the "
			"amount is inferable, and one account used at one shop for a year is "
			"a purchase history.",
		),
		(
			"There is no owner and no upgrade path.",
			"Nobody can alter these rules after the fact — not the merchant, not "
			"the author. Fixing anything means a new component, and balances do "
			"not move to it automatically.",
		),
	]


def earning_only_notice():
	"""The single most important thing a surface must not get wrong."""
	return (
		"EARNING ONLY. Points accrue and cannot be devalued — that promise is "
		"real and a customer can check it themselves. SPENDING THEM DOES NOT "
		"WORK YET: a redemption needs the customer to co-sign, and no wallet in "
		"reach can do that. Do not tell a customer they can spend these."
	)


def check_it_yourself(facts, account=""):
	"""The literal URLs a customer can open to check the promise themselves."""
	base = indexer()
	lines = [
		("The contract itself", f"{base}/substates/{facts['component']}"),
		("The points resource", f"{base}/substates/{facts['points_resource']}"),
	]
	if account:
		lines.append(("Your account", f"{base}/substates/{account}"))
	return lines
