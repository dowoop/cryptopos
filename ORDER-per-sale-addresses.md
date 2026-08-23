# ORDER — the terminal derives a fresh receiving address for every sale

STATUS: OPEN
OWNER: one builder. Nothing else may edit these paths while it is open.

## WHY

`DECISIONS.md` D5 switched `btc` off. Its adapter refuses a recipient with any
transaction history, this terminal had no per-sale address source, and the
seven sequences in that entry are why the refusal is right rather than
inconvenient — the first needs no misuse at all, only a payment broadcast for
an earlier sale and confirmed during the next one's window.

`cryptopos_core.hd` now supplies the missing half: BIP-32 CKDpub from a
watch-only extended public key, BIP-84 P2WPKH addresses, published-vector
tested, 274/274 lines executed and every mutant killed. This order is the
wiring, and turning `btc` back on is what it is for.

Read D5 before starting. Read `packages/cryptopos-core/src/cryptopos_core/hd.py`
for the surface you are calling.

## OWNS — the complete list of paths this order may create or modify
```
cryptopos/catalog.py
cryptopos/charge.py
cryptopos/install.py
cryptopos/harness.py
cryptopos/cryptopos/doctype/crypto_rail/crypto_rail.py
cryptopos/cryptopos/doctype/crypto_rail/crypto_rail.json
cryptopos/patches/derive_receiving_addresses.py
cryptopos/patches.txt
```

## READS — may be opened, must be left byte-identical
```
packages/cryptopos-core/src/cryptopos_core/hd.py
packages/cryptopos-core/src/cryptopos_core/addresses.py
packages/cryptopos-core/src/cryptopos_core/bitcoin.py
cryptopos/watch.py
cryptopos/settle.py
cryptopos/api.py
DECISIONS.md
```

**Nothing under `packages/` is modified.** If the core needs a change, stop and
write the question at the end of this file.

## WHAT TO BUILD

### 1. Two fields on `Crypto Rail`

- `testnet_xpub` (Data) — a watch-only **account-level** extended public key.
  For BIP-84 testnet that is the key at `m/84'/1'/0'`; the terminal derives the
  external chain `0/i` beneath it and nothing else.
- `next_address_index` (Int, read-only in the form) — the next `i`. The
  operator does not edit this; the terminal owns it.

`crypto_rail.py`'s `validate` must refuse:

- an `xpub`/`zpub` (mainnet version bytes) on this field, because the field is
  named `testnet_xpub` and a mainnet key here would derive addresses on a chain
  this terminal refuses to charge on;
- any key `hd.parse_extended_key` rejects, reported in that refusal's own words
  rather than a generic message;
- a rail that has **both** `testnet_xpub` and `testnet_recipient` set — the two
  are different bindings and a rail must say which one it means.

### 2. Derivation, and an index nothing can hand out twice

`catalog.recipient_for(rail, mode)` keeps its signature and gains a per-sale
path. When the rail carries a `testnet_xpub`:

- take `next_address_index`, derive `0/{index}` with `hd.derive_path`, and
  return `hd.p2wpkh_address(child, "tb")`;
- advance the index in the same database transaction.

**The index must be allocated under a row lock on the rail** —
`SELECT ... FOR UPDATE` on that `Crypto Rail` row — for the reason set out in
D5's closing paragraph: `poll` is reachable from both the scheduler and a
whitelisted endpoint, and two workers that read the same index would hand two
customers the same address, which is the exact defect this order exists to
remove. A test that only calls it sequentially does not test this; say in a
comment what serialises it.

A rail with a `testnet_recipient` and no xpub keeps today's behaviour and is the
**shared-address** binding. That is what the EVM rails still use, and D5 says
why they are tolerated: this terminal is testnet-only by charter. Do not change
them here.

### 3. The sale says which binding it got

`charge` already writes `binding`. It must now write `"per-sale"` when the
address was derived and `"shared"` when it came from `testnet_recipient`. The
field's options already contain both values; no doctype change is needed for it.

### 4. The gap limit is a ceiling, and ceilings ship on the surface

Every derived address is shown to a customer; not every one is paid. A run of
unpaid sales leaves a run of unused addresses, and a wallet restored from the
xpub stops scanning after **20** consecutive unused ones (BIP-44's gap limit) —
so money paid to an address beyond the gap is money the operator's own wallet
will not find.

Count the consecutive most-recent sales on the rail that ended without credited
money, and expose the number. It must appear in `api.rails()`'s row for the
rail. **Do not refuse a charge because of it** — refusing to take money because
of a wallet-scanning convention would be the terminal overreaching. Warn, name
the number, and let the operator act.

### 5. Turn `btc` back on

In `install.py`, `btc`'s seed row becomes `enabled = 1`. A rail with neither an
xpub nor a recipient still refuses at charge time, which stays the honest gate.
Add `cryptopos/patches/derive_receiving_addresses.py` to reload the doctype and
re-run `seed_rails`, and register it in `patches.txt` under `[post_model_sync]`.

## PROOF — harness checks, and they are the deliverable

Add to `cryptopos/harness.py`. It borrows and restores rail fields already; add
`testnet_xpub` and `next_address_index` to `_BORROWED_RAIL_FIELDS` so a run
cannot leave the operator's counter moved.

Use **BIP-84's published account key** so the expected addresses are known
values rather than values this code produced:

```
zpub6rFR7y4Q2AijBEqTUquhVz398htDFrtymD9xYYfG1m4wAcvPhXNfE3EfH1r1ADqtfSdVCToUG868RvUUkgDKf31mGDtKsAYz2oz2AGutZYs
```

It is a `zpub`, so §1's refusal rules must let it through or you must convert it
— decide, do it deliberately, and say in a comment which you did and why.

The checks, at least:

- charging twice on `btc` produces **two different** receiving addresses;
- neither address had been used by any earlier sale;
- the rail's index advanced by exactly two;
- the sale records `binding == "per-sale"`;
- a rail with a `testnet_recipient` and no xpub still records `"shared"`;
- a mainnet `xpub` on `testnet_xpub` is refused, in words that say why;
- a rail with both fields set is refused;
- `api.rails()` reports the gap run for the rail.

## INVARIANTS

- No new dependency anywhere.
- Nothing under `packages/` changes.
- `make lint` clean and `make check` green — you are not changing the package,
  so the package's gates must be untouched by your work.
- The live harness passes with **no** failures, on the real network.

## DONE WHEN
```bash
make lint
make check
docker exec frappe_docker-backend-1 bash -lc 'cd /home/frappe/frappe-bench && bench --site erp.localhost migrate'
docker exec frappe_docker-backend-1 bash -lc 'cd /home/frappe/frappe-bench/sites && ../env/bin/python -c "
import frappe; frappe.init(site=\"erp.localhost\"); frappe.connect()
from cryptopos import harness; harness.run()"'
```
Paste the real output of each into the RESULT section below.

## NOT IN THIS SLICE
- Per-sale addresses for the EVM rails → different derivation (BIP-44 +
  keccak), and `hd.py` has no EVM address function. Separate order.
- Any change to `watch.py` or `settle.py` → they read `identity_address` off
  the sale and do not care where it came from.
- Sweeping funds, spending, or anything that needs a private key → never, in
  this repository.

---
## RESULT — filled in by the builder, not before
