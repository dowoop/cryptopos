# DECISIONS.md — what was decided, and what it cost to find out

Each entry names a position, says whether it was taken or rejected, and gives
the evidence. A decision without a reproduction is a preference; those do not
go here.

---

## D1 · A settled sale that fails to book must be retried — TAKEN, 2026-08-23

`settle.book` was called once, from the watcher, at the instant a sale settled.
Nothing called it again: `confirmed` is terminal, `poll` returns immediately for
it, and `heartbeat` selects only sales still in flight. A booking that failed
stayed failed, in silence, with the money already received.

**Reproduced, not argued.** Pointing the terminal's Item at a name that does not
exist makes `Sales Invoice.insert()` raise `LinkValidationError`; the sale stays
`confirmed` with no invoice and nothing re-polls it. Ordinary configuration
reaches this — a renamed Item, a closed fiscal year, an accounting dimension
made mandatory this morning.

`book` now catches the ledger's refusal inside a savepoint and writes the reason
onto the sale; `settle.sweep_unbooked` retries every five minutes;
`settle.unbooked` (whitelisted as `api.unbooked`) answers the same question for
an operator.

## D2 · Booking requires a transaction id — TAKEN, 2026-08-23

The booking equation was four terms: mode AND provenance AND state AND
identity_source. It is now five. A sale nothing can trace back to a transaction
is not evidence of revenue.

**This is what made D1's sweep safe rather than dangerous.** At the time it was
written the site held 44 settled sales with no invoice and no `tx_id` — harness
residue, none of it real money — and a sweep trusting the old four terms would
have written **$1,101,650** of fiction into the ledger on its first pass. All 16
sales that genuinely booked satisfy the new equation, so nothing legitimate is
excluded.

`book` writing `"txid: not recorded"` into the invoice remarks was the tell that
this was known to be possible and tolerated. The core agrees and always did:
`SettlementDecision` refuses to be `SETTLED` without credited money *and*
transaction ids.

## D3 · The app drives `cryptopos_core.catalog`, not its own watcher — TAKEN, 2026-08-23

`charge.py` carried `URI_BUILDERS = {"bitcoin": ...}` and `watch.py` carried
`WATCHERS = {"bitcoin": ...}`, each with one entry, while the package the app
already depends on held adapters reaching four live testnets through one
contract. `cryptopos/catalog.py` is the seam that was missing.

Two defects died with the old code:

- **The charge path used the primitive.** `rates.native_for` divides straight to
  native precision and can produce an amount no URI can state. Measured across
  the rail table, **five of twelve rails** disagree with `rails.invoice_amount`
  — ETH, POL, SOL and XMR by construction, because their display and native
  decimals differ. On a decimal-amount rail the QR would ask for less than the
  sale expects. The charge path now uses `invoice_amount`, which rounds once at
  display precision and then scales.
- **Attribution was a clock comparison.** The old watcher decided a transaction
  was eligible by comparing its block timestamp to the charge time. It is now
  the captured baseline chain position, read before the payer is shown anything.

## D4 · `native` stays an `int` in `tender` records — REJECTED the change, 2026-08-23

Raised in the `tender` library as QUESTIONS.md Q18: make `to_record` emit a
decimal string because JavaScript loses integers above 2^53. Rejected after
Codex attacked it, on facts reproduced in this repository:

`charge.py` already writes `str(invoiced_native)`; `crypto_sale.json` types
every native as `Data`; `terminal.js:607` reads them back with
`BigInt(value || "0")` under a comment saying *"BigInt because satoshis fit in a
double and wei does not"*. **This app had already built the correct boundary.**
Changing the library would also have made `to_record` partial where it is total
(`str()` raises above 4300 digits) and would have accepted records the very
JavaScript consumer cannot read (`int("1_000")` is 1000; `BigInt("1_000")`
throws).

## D5 · A shared receiving address cannot be made safe by bookkeeping — REJECTED the opt-in, 2026-08-23

The `bitcoin:testnet4` adapter refuses `capture_baseline` on an address with any
transaction history. The app has no per-sale address source, so BTC charging
refuses outright. The proposal was an explicit opt-in —
`configuration={"allow_reused_recipient": True}` — on the grounds that
attribution by captured baseline tip is stronger than the timestamp comparison
the app used before.

**Rejected.** It is stronger as a statement about mining order and is not a
payment binding. The decisive sequence needs no misuse at all:

1. An earlier customer broadcasts `T` paying the shared address.
2. Their sale expires before `T` confirms — so it holds no `tx_id`, and the
   claimed-transaction defense has nothing to work with.
3. A new sale captures a baseline at the current tip.
4. `T` confirms one block later, inside the new sale's window.
5. It is credited to the new sale, which settles and books.
6. The new customer paid nothing and walks away.

Late confirmation is ordinary Bitcoin behaviour. Six further sequences were
given and each holds: overlapping sales are assigned by polling order rather
than by attribution; `settle` aggregates *every* unclaimed transfer after the
baseline rather than selecting one matching the invoice; a baseline stores a
height rather than a block hash, so a reorganisation can lift an old payment
above it; and the single unpaginated address read cannot even see far enough
back on a busy address.

The assumption underneath is address exclusivity under another name. A fresh
address provides it; a shared address does not.

**What this costs, stated plainly.** `btc` is seeded and switched **off**. The
rail is described and not offered. Restoring it needs a per-sale address source
— BIP32/BIP84 derivation from an operator xpub, or an address lease — and that
is the next piece of work on this rail, not a configuration flag.

**Two consequences worth carrying forward.**

- **The EVM adapters do not refuse address reuse**, and their own rail table
  calls their binding *"static address + exact-amount match in the lock window
  (weakest)"*. Sequences 1–3 above apply to them too. They are enabled because
  this terminal is testnet-only by charter and mainnet is refused by decision —
  **not** because the binding is sound. Anything approaching real money needs
  per-sale addresses on every rail.
- **Two defects in the new watcher came out of the same review** and are fixed:
  it persisted only `decision.transaction_id` and discarded the rest of a
  multi-transfer settlement, leaving those transactions looking unclaimed to the
  next sale; and it read the claimed set without holding it, so two workers
  could both settle on one transaction. The set is now read `FOR UPDATE`, and
  `identity_address` and `tx_id` are indexed so the lock is on those rows.

## D6 · `tender` is not wired into this app — REJECTED, four times, 2026-08-23

Four designs were proposed for making the `tender` exact-money library a real
consumer inside cryptopos. Codex was asked to attack each cold. **All four
lost, and every decisive claim was reproduced here before being accepted.**

| proposal | what killed it |
|---|---|
| `to_record` emits a decimal string (Q18) | this app had already built the correct boundary; the change would make `to_record` partial where it is total |
| `tender` as the accounting authority | dual authority — converting 17,171 sat back at an exact rate gives $10.98 against a charged $10.99, and `"exact"` raises |
| one `tender.Asset` per Crypto Rail | three USDC rails collapse to one `Asset`; `Amount(1e6, usdc_eth) + Amount(1e6, usdc_sol)` succeeds |
| a crypto position report | it would value a faucet token at the mainnet price |

**The root cause is the same every time, and it is worth stating once.**
`tender.Asset` is `(code, exponent)`. This application's world is
`(chain, contract, network, mode)`. One asset code cannot carry that, and the
gap is not cosmetic:

```
one Sepolia ETH valued at rate("3500.00", ETH, USD) -> 3500.00 USD
```

Reproduced. The terminal refuses mainnet by decision, so **every** bookable
payment it takes is a test token — and the honest price for a test token is
zero, which `rate()` refuses (`ValueError`). A report built this way must either
fail or invent a positive valuation for a faucet.

That is exact arithmetic over an invalid domain model, and it is *worse* than
approximate arithmetic over a valid one, because the exactness makes the false
number look authoritative.

**What was kept.** `tender` is a correct and installable library and was proven
so in the actual target environment: its wheel installs into the Frappe
container's Python 3.14 and **193 of its own tests pass there against the
installed copy with `src/` deleted**. It is simply not this application's
library. `cryptopos_core` already owns exact integer money math for these rails,
with every line executed and 1809/1825 mutants killed.

**How to apply:** do not re-propose wiring `tender` in. If a fifth design
appears, it must first answer how an `Asset` distinguishes Sepolia ETH from
mainnet ETH, and what the report does with a rate of zero.

## D7 · Every Bitcoin sale gets its own address — TAKEN, 2026-08-23

D5 switched `btc` off for want of a per-sale address source.
`cryptopos_core.hd` supplies it — BIP-32 CKDpub from a watch-only account key,
BIP-84 P2WPKH, published-vector tested — and this is the wiring. `btc` is on
again, and its binding is now `per-sale` rather than `shared`.

**Public keys only, and the module cannot be talked out of it.**
`parse_extended_key` refuses `xprv`/`tprv` by version bytes before it looks at
any key material. The rail's field refuses a mainnet `xpub`/`zpub`, refuses a
key that is not account-level, and refuses a rail configured with *both* an
xpub and a fixed recipient — those are two different bindings and a rail must
say which it means.

**The index is allocated under a row lock on the rail.** `poll` and `charge`
are both reachable from the scheduler and from a whitelisted endpoint, and two
workers reading the same index would hand two customers the same address —
which is the defect this whole line of work removes. A sequential test passes
either way, so the lock is written deliberately and commented as such.

**The gap limit is surfaced, never enforced.** Every derived address is shown
to a customer and not every one is paid, so unpaid sales leave unused addresses
behind; a wallet restored from the account key stops scanning after 20 of them.
`api.rails()` reports `gap_run` and `gap_limit` per rail. It does not refuse a
charge — declining a customer's money over a wallet-scanning convention would
be the terminal overreaching.

**Two things the harness had to be taught, and both were findings.**

- **A published test vector cannot prove freshness.** BIP-84's account key is
  one of the most widely known in existence and its addresses carry real
  testnet history, so the adapter refused them. The harness now uses two keys
  for two jobs: the published one proves the *derivation* against numbers a BIP
  published, and a minted, never-used account key proves the terminal hands out
  a *fresh* address. Nothing is ever expected to arrive at the second.
- **The seeded maturity notes described a different application.**
  `cryptopos_core.rails` defines "works" as real testnet reads **and a real
  payer**, and its notes came over verbatim from the tkinter terminal, which
  bundled a wallet that signed and broadcast. This app has no signing path
  anywhere. Three rails were telling an operator "real payer (bundled wallet
  signs & broadcasts)". They now say what is true here: real reads on the named
  network, watch-only, the customer's own wallet is the payer, and the binding
  named — including "weakest" where that is what it is.

`seed_rails` refreshes rail *prose* on every migrate for that reason, and
deliberately never rewrites `asset`, `family`, `unit_name` or either decimals
field on an existing rail: a `credited_native` is an integer whose meaning comes
from the decimals in force when it was written. A disagreement there is
reported as drift, not silently repaired.

  harness: 55 checks, 0 failures, live network — was 44.

## D8 · The takings an operator can see, and the ones they cannot — TAKEN, 2026-08-23

Everything the terminal knew about its own money was reachable only by reading
`Crypto Sale` rows one at a time. `api.unbooked` answered the most important
question and answered it to a JSON caller, not to the person at the desk.

There is now a `Crypto Takings` script report (one row per rail per day), two
number cards, and a 30-day chart on the workspace. Three rules shaped every one
of them, and each was already paid for:

- **No native amount is ever converted to a currency.** D6, four times over: an
  asset code carries no network, this terminal refuses mainnet, so every payment
  it can book is a test token and a valuation column would be a
  false-but-authoritative number.
- **`credited_native` is text and never crosses rails.** Wei plus satoshi is a
  number that means nothing, and an 18-decimal daily total exceeds 2^53 in a
  desk that renders through JavaScript. D4.
- **Booked and unbooked USD are separate columns, never one total.** The gap
  between them is the number D1 exists to keep visible.

The number cards are labelled *"Settled, not yet in the ledger"* rather than
"unbooked", because a category name does not tell an operator that a non-zero
value is a problem.

**Rail health is asked for, never polled.** `readiness` makes a network call per
rail and `api.rails()` runs on every terminal page load, so it is behind
`with_readiness=0`. A till that hangs at the counter because a public RPC is
slow is worse than one that does not show rail health.

**And one hazard the harness introduced, closed.** `_settle_by_hand` fabricates
a sale satisfying all five booking terms with an invented transaction id —
which is what lets the booking half be tested at all, since nothing here can
make a payment. But the scheduler sweeps every five minutes, so a run overlapping
a sweep would book fiction, and a run that died before `_cleanup` would leave the
invoice behind. The harness now stops that scheduled job for the duration and
gives it back, exactly as it borrows the operator's settings, and asserts that
it is stopped while it runs.

  harness: 68 checks, 0 failures, live network — was 55.

**The correction worth recording.** This order's first `OWNS` list put the
report at `cryptopos/report/`, which Frappe never syncs, and asked for number
cards and charts to be defined inside the workspace file, which cannot define
them. The builder stopped and filed the question instead of guessing, and it was
right: reports live at `<app>/<module>/report/<name>/`, and `number_card/` and
`dashboard_chart/` are sibling module directories holding their own records.
Verified against the running site before the order was corrected.
