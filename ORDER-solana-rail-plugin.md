# ORDER — a Solana devnet rail, as a separately installed plugin

## Why this order exists

`cryptopos/catalog.py` now discovers rail adapters from `cryptopos.rails`
entry points, so an operator can add an asset by installing a wheel and
creating a `Crypto Rail` row instead of editing this app. **Nothing has ever
been installed that way.** A discovery path with no plugin behind it is a
claim, not a capability, and this order is the first real one.

Solana devnet is the right first plugin for three reasons that were measured,
not assumed:

1. `cryptopos_core.catalog.solana_devnet` already exists as a `RequestRail`
   whose blocker says *"the provider-specific observer has not been extracted
   into this package"*. It can build a QR and cannot prove receipt.
2. The bundled payer already holds **2.453379 SOL on devnet** and
   `customer_wallet.can_pay("sol")` is True, so this rail can be driven end to
   end for free.
3. **Solana Pay binds a payment to a sale.** Every other EVM rail here
   receives at one shared address — D5, still open. Solana's `reference`
   account makes the binding collision-proof, which puts this rail alongside
   `btc` (D7) rather than alongside the three that cannot attribute.

## What to build

A **separate distribution** at `packages/cryptopos-rail-solana/`:

```
packages/cryptopos-rail-solana/
  pyproject.toml
  src/cryptopos_rail_solana/__init__.py
  tests/
```

- Package name `cryptopos-rail-solana`, module `cryptopos_rail_solana`.
- `requires-python = ">=3.9"`, and **`dependencies = []`**. Standard library
  only. It may import `cryptopos_core`; declare that as its one dependency.
- Entry point:
  ```toml
  [project.entry-points."cryptopos.rails"]
  solana-devnet-sol = "cryptopos_rail_solana:solana_devnet_sol"
  ```
- It must **not** import `frappe`. It is a library.

## The contract it must satisfy

`cryptopos_core.plugin.PaymentRail`, with **all four** capabilities:
`ADDRESS_VALIDATION`, `PAYMENT_REQUEST`, `OBSERVATION`, `SETTLEMENT`.

`cryptopos_core/evm.py` is the reference implementation — read it first. It is
the closest complete adapter and it already passes
`cryptopos_core.conformance.require_conformant`. Match its shapes exactly:
`Readiness`, `RecipientBaseline`, `ObservationBatch`, `TransferObservation`,
`SettlementDecision`. Do not invent fields.

`network = Network("solana", "devnet", True)`,
`asset = Asset("native", "sol", "DevnetSOL", 9)`, so
`key == "solana:devnet/native:sol"`.

Reuse what already exists rather than rewriting it:
`cryptopos_core.addresses.validate("sol", ...)` for the address, and
`cryptopos_core.uri.build_uri("sol", identity, amount, "testnet")` for the
Solana Pay URI.

## The binding — the one design decision this order makes

Solana Pay's `reference` is a 32-byte public key the payer includes as an
extra account on the transfer. The watcher then asks
`getSignaturesForAddress(reference)`, and only this sale's money touches it.

`PaymentIntent.payment_reference` is an **invoice string**, not a pubkey, so
the plugin must derive the reference key itself:

> **`reference = base58(sha256(intent.intent_id))`**, computed the same way in
> `create_request` and in `observe`.

Derived, never stored: `observe` receives the intent and must be able to
recompute it with no extra state. It must be a pure function of `intent_id`
and nothing else — not of the clock, not of `random`, not of the recipient —
because a reference that differs between the request and the watch is a sale
that can never be seen to have been paid. Assert that property in a test.

## The Solana reasoning that is already paid for

Every item below was found the expensive way in another terminal against this
same devnet. Reproduce the behaviour; do not re-derive the lessons.

1. **The rail picks the branch; the data does not get a vote.** This rail
   names no token mint, so credit comes from the **lamport** balance diff and
   it must never read `postTokenBalances`. A SOL payment whose transaction
   also touches a token account was once credited out of that token's delta.

2. **`meta` is nullable.** `getTransaction` can return the transaction with
   `meta: null`. That is *unknown*, never zero. Reaching into it raises a
   `TypeError` that a poller paints as "node unreachable" about a node that
   answered fine.

3. **Unknown is a real answer and must be representable.** A malformed or
   incomplete node answer — no `accountKeys`, balance arrays shorter than the
   account list, a non-integer lamport balance — reports unknown with a
   reason. It never guesses an amount. A zero credited against a finalized
   signature produces a part-paid decision about a customer who paid in full.

4. **Address lookup tables.** A v0 transaction can load accounts from a lookup
   table; those arrive in `meta.loadedAddresses` and are **not** in
   `accountKeys`. Do not decode them and do not guess — say the amount cannot
   be read. Pass `maxSupportedTransactionVersion: 0` on `getTransaction` or a
   real node refuses versioned transactions outright.

5. **Order by `slot`, never by list position.** `getSignaturesForAddress`
   returns newest-first on a real node. Bind the **earliest** signature by
   slot, so a receipt does not rename its transaction when a second payment
   lands.

6. **Split payments.** Every transaction touching the reference is this sale's
   money. Sum them all; crediting only the first under-reports a paid sale.

7. **A failed transaction pays nothing** — fees come from the payer. Skip
   entries with a non-null `err`, but if *every* entry failed, say so.

8. **Commitment.** `finalized` settles. `confirmed` may be read to display an
   amount and must never settle. `processed` is unreachable on devnet — it
   answers `-32602 "Method does not support commitment below 'confirmed'"` —
   so do not build a branch that pretends otherwise.

## What it must not do

- No simulation, no fixture that stands in for the chain, no `time.sleep`.
- No `settle()` that returns SETTLED on anything short of `finalized`.
- No dependency, direct or transitive.
- No network call at import time.

## Gates

1. `python3 -c "from cryptopos_core.conformance import conformance_issues; ..."`
   returns `()` for the plugin against a devnet configuration.
2. `cryptopos_core.registry.validate_plugin(solana_devnet_sol)` passes.
3. Its own tests run with `python3 -m unittest discover` from the package root
   and cover: the reference derivation is deterministic and pure; each of the
   unknown-answer shapes in item 3 returns unknown rather than a number; a
   failed transaction credits nothing; the earliest-by-slot binding survives
   both list orders; two transactions to one reference sum.
4. Every test must run **offline**. Put the node behind one seam so it can be
   substituted; do not reach the network from a unit test.

Report what you built, which gates you ran, and their real output. If any
instruction above turns out to be wrong against the actual contract in
`cryptopos_core/plugin.py`, say so and follow the contract — the contract is
the authority, this order is a brief.


---

## LANDED 2026-08-25

Built by Codex against this brief; verified here rather than accepted.

**What was checked, and how:**

| claim | how it was checked | result |
|---|---|---|
| conformance | `conformance_issues(rail, devnet_config)` | `()` |
| plugin contract | `registry.validate_plugin` | ok |
| all four capabilities | `readiness` against `https://api.devnet.solana.com` | ready, `chargeable: True` |
| its own tests | `python3 -m unittest discover` | 14 passed, offline |
| discovery | `pip install` + `catalog.plugins()` on the deployment | 6 driveable, identity stamped |
| end to end | `prove_end_to_end.py --rail sol --cents 1 --send` | `CPS-2026-00328` → `ACC-SINV-2026-00075` |

**One defect found, and the tests could not have caught it.**
`DEVNET_GENESIS_HASH` was the real hash truncated at 32 of its 44 characters,
and `_verify_network` compares it against `getGenesisHash` — so the rail would
have refused every real devnet node as not-devnet. All fourteen tests passed
because none of them touches a node. Found by asking the chain:

```
{"jsonrpc":"2.0","result":"EtWTRABZaYq6iMfeYKouRu166VU2xqa1wcaWoxPkrZBG","id":1}
```

**Two deviations from the brief.** `dependencies = []` while the module imports
`cryptopos_core` — accepted, because `cryptopos_core` is not on any index and is
path-installed here, so declaring it would break the install; the README states
it instead. And a lint autofix was run across three files the order had told it
not to touch; those were reverted, and then re-applied when `make lint` showed
they were fixing real pre-existing errors rather than churning.

**Added after the build:** `binds_per_sale = True`, so `tools/rails_probe.py`
stops reporting the one rail with a real payment binding as an unbound shared
address.
