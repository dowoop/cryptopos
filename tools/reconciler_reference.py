"""A SECOND, INDEPENDENT answer to "how much of this transaction paid this sale".

The rail in `packages/cryptopos-rail-solana` is the first. This is the second,
and `tools/attribution_agreement.py` drives both against the same recorded
vectors so that a fix to one cannot silently leave the other wrong.

**Why a second implementation at all.** Three programs used to answer this --
the rail, a reconciler in another repository, and the checkout terminal -- and
that separation is the whole point: a reconciliation that shares an
implementation with the thing it reconciles proves nothing. Two of those three
were retired on 2026-09-04, and this is the reconciler's copy, lifted VERBATIM
so it stays a genuinely different piece of code rather than a paraphrase of
the rail.

It reads only. It imports nothing from this package, deliberately -- the moment
it shares a helper with the rail it stops being independent evidence and starts
being the same answer twice.

**It has been wrong, which is the argument for keeping it.** Its own comments
below record two defects `attribution_agreement.py` caught here: crediting one
transfer to two sales when a payment named two references, and claiming more
than the balance delta could support. Both were found by disagreement, not by
review.
"""

import json
import urllib.request

SOLANA_RPC = "https://api.devnet.solana.com"
SOLANA_RAILS = frozenset({"sol"})
SYSTEM_PROGRAM = "11111111111111111111111111111111"
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"

def _base58(text):
	"""base58 text -> bytes, or None if it is not base58."""
	if not isinstance(text, str) or not text:
		return None
	value = 0
	for character in text:
		digit = _B58.find(character)
		if digit < 0:
			return None
		value = value * 58 + digit
	body = value.to_bytes((value.bit_length() + 7) // 8, "big") if value else b""
	return b"\x00" * (len(text) - len(text.lstrip("1"))) + body

AGENT = {"User-Agent": "cryptopos-reconciler-reference/1.0",
         "Content-Type": "application/json"}

def _rpc(url, method, params):
	body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
	request = urllib.request.Request(url, data=body, headers=AGENT)
	return json.loads(urllib.request.urlopen(request, timeout=30).read()).get("result")




def _solana_credit(signature, address, reference=None):
	"""What this signature paid `address`, in lamports, or None if unreadable.

	`maxSupportedTransactionVersion` is not optional: a real node refuses to
	return a versioned transaction at all without it. And a v0 transaction can
	load accounts from a lookup table, which arrive in `meta.loadedAddresses`
	and NOT in `accountKeys` -- so an address that is genuinely absent from the
	list is unreadable here rather than unpaid. Returning 0 for that would be a
	number, and a wrong number outranks a missing one in every direction that
	matters.
	"""
	transaction = _rpc(SOLANA_RPC, "getTransaction",
	                   [signature, {"encoding": "json",
	                                "maxSupportedTransactionVersion": 0,
	                                "commitment": "finalized"}])
	meta = (transaction or {}).get("meta")
	if meta is None:
		return None
	keys = ((transaction.get("transaction") or {}).get("message") or {}).get("accountKeys")
	if not isinstance(keys, list) or address not in keys:
		return None
	index = keys.index(address)

	# CREDIT WHAT THE SALE'S OWN TRANSFER MOVED, when the capture told us what
	# bound it. The rail counts only System transfers carrying this sale's
	# Solana Pay reference (cryptopos D33), so a reconciler that summed the
	# recipient's whole balance delta would flag a mismatch on a transaction the
	# terminal attributed correctly -- one transaction, two answers, and the
	# oversight tool disagreeing with the thing it is meant to be checking.
	if reference in keys:
		instructions = ((transaction.get("transaction") or {}).get("message") or {}).get("instructions")
		if isinstance(instructions, list) and SYSTEM_PROGRAM in keys:
			system, wanted = keys.index(SYSTEM_PROGRAM), keys.index(reference)
			total, found = 0, False
			ambiguous = False
			for instruction in instructions:
				accounts = (instruction or {}).get("accounts")
				if (instruction or {}).get("programIdIndex") != system:
					continue
				if not isinstance(accounts, list) or len(accounts) < 2:
					continue
				if accounts[1] != index or wanted not in accounts:
					continue
				# EXACTLY ONE REFERENCE, the same rule the rail applies. Solana
				# Pay permits several references on one transfer, so a payment
				# naming two sales belongs to neither -- and the first version
				# of this function, written to APPLY D33, left this out and
				# credited the same 100-lamport transfer to both. The rule and
				# its copy drifted apart inside the change that created them.
				if [a for a in accounts[2:] if a != wanted] or accounts.count(wanted) != 1:
					ambiguous = True
					break
				data = _base58(instruction.get("data"))
				if data is None or len(data) != 12 or int.from_bytes(data[:4], "little") != 2:
					continue
				total += int.from_bytes(data[4:], "little")
				found = True
			if ambiguous:
				return None
			if found:
				# The instructions must not claim more than the balance shows.
				# The rail refuses that and this did not -- found by
				# cryptopos/tools/attribution_agreement.py on its first run,
				# which is the whole reason that gate exists.
				before, after = meta.get("preBalances"), meta.get("postBalances")
				if (isinstance(before, list) and isinstance(after, list)
						and index < len(before) and index < len(after)
						and isinstance(before[index], int) and isinstance(after[index], int)
						and after[index] - before[index] < total):
					return None
				return total
			# A reference was supplied and no referenced transfer paid us. The
			# balance delta below would answer a DIFFERENT question -- what the
			# transaction did to the address -- and returning it here is how the
			# rail and the reconciler disagree about one transaction.
			return None

	before, after = meta.get("preBalances"), meta.get("postBalances")
	if not isinstance(before, list) or not isinstance(after, list):
		return None
	if index >= len(before) or index >= len(after):
		return None
	if not isinstance(before[index], int) or not isinstance(after[index], int):
		return None
	return after[index] - before[index]
