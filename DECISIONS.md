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

### D8 note · the residue was cleared, and only after it was proved to be residue

D8's number cards immediately read **44 settled sales, $1,101,650 not in the
ledger** — an alarming figure on a brand-new dashboard, and a false one. Those
were the sales D2 already identified: created 15–17 August by the harness that
had no cleanup.

Every one of the 44 was re-checked individually before anything was removed, and
the deletion refused any sale failing a single condition:

    event-source sets : {('harness',): 44}
    with no tx_id     : 44 / 44
    with a book event : 0
    refused to touch  : 0

They are gone. The card now reads **0**, which is both the healthy reading and
the true one. What remains is 16 confirmed sales, all 16 carrying an invoice,
plus 17 expired and 14 parked for review — every one a real ending.

Leaving them would have been the worse error: an oversight surface whose first
impression is a false million-dollar hole teaches an operator to ignore it.

## D9 · Per-sale addresses on the EVM rails — REJECTED, 2026-08-23

D5 says a shared receiving address cannot be made safe by bookkeeping, and D7
fixed that for `btc`. The obvious next step — do the same for `eth`,
`usdc-eth` and `usdc-pol` with BIP-44 and keccak — was attacked and lost.

**The Bitcoin analogy does not carry.** A hundred derived Bitcoin addresses are
a hundred UTXOs that one signed transaction sweeps. An EVM account cannot be an
input to somebody else's transaction: each derived address must originate and
sign its own, and an address holding **only USDC has no ETH to pay the gas**.
Collecting a hundred small USDC sales means a hundred funding transactions and
a hundred signed transfers — from a terminal that has no signer at all, by
design. A small sale can cost more to collect than it contains.

**Two consequences were found in what D7 already shipped, and both reproduced.**

- **Two rails sharing one account key derive the same address.** The index is
  rail-local, so both start at zero. Measured here: `btc` and `eth` given one
  key both returned `tb1qjmalnk7asntx02x2r3e30x0p7h3rsc2rs9hvrg`. That is D5's
  collision again, across assets. A rail now refuses a key another rail holds.
- **A key on a non-deriving rail produced a Bitcoin address.** Only BIP-84
  P2WPKH exists here, so an EVM rail given a key was handed a bech32 address as
  its Ethereum recipient. The adapter refused it at charge time, so it failed
  safe — but it failed at the counter rather than at the form. Refused at the
  form now.

**One consequence accepted and not yet fixed.** A payment that confirms after
its sale expires is invisible: `poll` returns immediately for terminal states,
the heartbeat selects only in-flight sales, and no later sale watches a
retired address. On Bitcoin the money is still the operator's — their wallet
scans the branch — but the terminal never sees it and the customer gets no
receipt. Named here rather than left to be discovered. On the EVM rails the
same payment would be stuck as well as invisible, which is part of why this
was rejected.

**A note on two rules that are now unreachable.** The "both bindings at once"
refusal can no longer fire through any rail that exists — a bitcoin rail
refuses a fixed recipient, and every other family refuses a key. It stays in
`validate` as the general statement, and the harness says in a comment why it
is not asserted. The twin-key rule needed a second deriving rail to exercise at
all, so the harness makes one and takes it away again.

**What a real EVM answer would need:** a payment contract or processor binding
an invoice id and forwarding with customer- or relayer-paid gas, or a complete
account-management system — global allocation, permanent branch monitoring,
verified derivation metadata, signer, gas station, sweeper, dust accounting.
Address generation alone is not the feature.

## D10 · A late payment becomes a record, not a reopening — TAKEN, 2026-08-23

D9 named this and left it: a payment confirming after its sale's lock ran out
is invisible. `poll` returns immediately for a terminal state, the heartbeat
selects only sales still in flight, and on a per-sale address nothing looks
again. The customer paid, the sale says expired, and no surface mentions it.

`cryptopos/reconcile.py` looks again, hourly, and the shape of it is the
constraint:

**Nothing reopens a sale.** `LEGAL` gives terminal states no transitions on
purpose — a sale that has already told a customer something is not edited
afterwards, and a correction is a new record. So the finding is an append-only
audit row plus an operator-facing list (`api.late_payments`). Honouring a late
payment is a new sale and refunding one is a transfer this terminal cannot
make; both are the operator's, and neither is something a sweep should do
quietly.

**Only per-sale addresses are reconciled.** On a shared address, money arriving
after a sale ended cannot be attributed to it — that is the whole of D5, and
guessing here would be the same mistake arriving through a later door.

**An unanswered endpoint reports zero found, not an error.** "Nobody looked"
and "nothing was there" differ inside a sale's lifetime, which is why
`watch.poll` is careful about it. This runs after that lifetime and will run
again in an hour, so the distinction has stopped paying for itself.

Proved against the chain rather than a mock: the harness points an ended sale
at a real testnet4 address carrying real confirmed payments, rewinds its
baseline below them, and the reconciler finds **3,000,000 satoshi**, records it
without moving the sale out of its ending, and declines to record it twice.

  harness: 76 checks, 0 failures, live network — was 71.

## D11 · A public, multi-visitor testnet demo on this app — REJECTED as specified, 2026-08-24

The long-horizon goal is one hosted ERPNext instance where any stranger can run
a testnet sale end to end with a prefunded wallet. A five-part architecture was
written down and attacked cold by Codex, which had not seen the goal before:

1. one merchant, `CryptoPoS Settings` stays Single;
2. BTC-only, because D7 gives it a fresh address per sale and D5 says the EVM
   rails cannot be attributed under the concurrency a public demo guarantees;
3. per-visitor isolation by row, via owner-based permissions;
4. the prefunded wallet is the customer side, and the sibling repository's
   payer already exists;
5. nothing new in `cryptopos_core`.

**Rejected.** Six findings; five reproduced here before being accepted, one
accepted on its mechanism only. Parts 2, 4 and 5 cannot all be true at once.

**The decisive one, and it needs no attacker (reproduced, measured live).**
`RATE_LOCK_SECONDS = 15 * 60` (`charge.py:24`), and `bitcoin.py` credits only
transfers whose `block_time_epoch <= intent.expires_at_epoch` — a confirmation
after expiry is `late`, never `timely`. Measured against
`mempool.space/testnet4` on 2026-08-24, over the 14 most recent intervals:

| | |
|---|---|
| median block interval | **20.0 min** |
| mean | 18.8 min |
| intervals longer than the 15-minute lock | **13 of 14** |
| chance an immediately-broadcast payment misses the lock | **~25%** |

Testnet4 keeps the 20-minute difficulty-reset rule, so ~20 minutes is where its
intervals *sit*, not an outlier.

**This was not a discovery, and saying so matters.** The sibling repository's
faucet registry has said it since **2026-07-23**, a month earlier, in the `btc`
entry's own words: *"testnet4 blocks are ~10 min, so expect the rate lock to run
out."* The `dash` entry quantified the same shape on 2026-07-26 — *"settling
takes ~14 min against a 20 min lock."* Two rails, written down, twice. What is
new here is the measurement (20.0 min median, ~25% stranded, re-runnable) and
the finding that it is **not recoverable** — D12, D13 and D14 close every route
to crediting the tail automatically. A known cost turned out to be a structural
limit. **Re-run it rather than believing this table:**
`make lockcheck` (`tools/lockcheck.py`) is that measurement, and it exits
non-zero while the share is above 5%. The lock is systematically shorter than the
chain it is used on. And D10's reconciler deliberately never reopens a sale, so
those sales never settle and never book — they become an audit row. **About one
honest, immediately-paid sale in four fails permanently, with a perfect payer.**
That is disqualifying for "anyone can complete a sale" on its own.

**The other reproduced findings.**

- **There is no Bitcoin payer, so a BTC-only demo has no payment path at all.**
  `customer_wallet.can_pay` returns true for `sol`, `usdc-sol`, `eth`, `pol`,
  `usdc-eth`, `usdc-pol` — and not `btc`. Found here independently before the
  attack landed. `wallets.py` has `BTC_MERCHANT_XPUB` (watch-only) and no
  customer key; `primitives.py` has secp256k1 with RFC-6979, `hash160`, bech32
  and BIP-32, but nothing builds a Bitcoin *transaction* — no BIP-143 sighash,
  no witness serialisation, no UTXO selection. The two concurrency-safe halves
  do not meet: BTC is the only rail safe under strangers, and it is the only
  rail the payer cannot pay.
- **The API has no concept of ownership.** `api.status(sale_name)` is
  `@frappe.whitelist()` with no role check and no permission check, and
  `frappe.get_doc` does not check permissions by default — any logged-in user
  reads any sale by name, including its address, amount, txid and invoice.
  `poll` checks only the broad `Sales User` role. `grep -c` for
  `has_permission|frappe.session.user|owner` across `api.py` returns **0**.
  **Since executed, not merely read** — `tools/isolation_probe.py` creates a
  disposable user with only `Sales User`, calls `api.status` on a sale owned by
  Administrator, and gets back the URI, the receiving address, the invoiced
  native amount and the price. It removes the user again and changes no sale.
  Run it before deciding step 1 of `GOAL.md`: a shop-per-visitor answer is only
  possible once that probe refuses.

  **And D19 made this worse, which is worth noticing.** When the probe was first
  run it leaked `uri`, `identity_address`, `invoiced_native` and `usd_cents` —
  `tx_id` and `sales_invoice` were empty on every sale, because no live sale
  could settle. Re-run after the clock fix, it leaks those two as well. **The
  isolation defect did not change; the amount of truth behind it did.** A
  deployment that starts working starts having more to leak.
  Owner-based DocType rules alone cannot fix this: `Crypto Sale Event` is a
  child table with no permissions of its own, and Crypto Takings uses
  `frappe.get_all`, which bypasses row permissions by design.
- **BTC address allocation is collision-safe but serialises across network
  I/O.** `catalog.recipient_for` takes `SELECT … FOR UPDATE` on the rail row
  (`catalog.py:135`), and the transaction then calls `validate_recipient` and
  `rates.quote` — a price-feed request — before any commit (`charge.py:95`).
  Two visitors cannot get the same address; they also cannot be charged
  concurrently. One slow feed blocks address allocation for everyone.
- **"EVM off" is a wish, not an invariant.** `install.py` seeds all four rails
  `enabled=1`, and migration deliberately never rewrites an existing rail's
  flag. Confirmed on the running instance: `btc`, `eth`, `usdc-eth`, `usdc-pol`
  all enabled, and **all three EVM rails share one recipient address** —
  D5's problem, tripled across rails. BTC-only has to be enforced at startup.
  `tools/rails_probe.py` reports this without waiting for a payment, and exits
  non-zero while it stands.

  **Corrected 2026-08-24: there is no cross-rail collision, and an earlier
  version of this entry claimed one.** The probe first grouped by
  (chain, address) and reported `eth` and `usdc-eth` taking each other's
  payments on Sepolia. They cannot. Reproduced in `evm.py`: the native observer
  accepts a transaction only when `to == recipient` **and** `value != 0`, and a
  USDC transfer has `to` = the token contract and `value` = 0; the token
  observer queries `eth_getLogs` with `"address": self.token_contract` and
  re-checks every log against it. **The two rails observe disjoint transaction
  shapes at one address.** Rails collide only when `catalog_key` *and* recipient
  both match, which is how the probe groups now. What is left is the real
  finding and it is unchanged: three rails each receive at a static address,
  which is D5 whatever else shares it.

**Accepted on mechanism, not reproduced end to end.** A visitor can flood
unpaid sales, and every in-flight sale is polled sequentially against public
Esplora each heartbeat, with no per-user quota and no poll throttle; a
rate-limited provider then pushes a legitimately paid sale to `needs_review`,
which is terminal. Each ingredient is verified; the starvation itself was not
run.

**What this changes.** The goal is not dead, but it is not a
deployment-and-permissions job, which is what part 5 claimed. Three things have
to exist first, and none of them is chain code in `cryptopos_core`:

1. **A rate lock that outlives a testnet4 block**, or settlement that does not
   depend on confirming inside it. This is the cheapest fix and the one that
   buys the most.
2. **A Bitcoin payer** — UTXO selection, BIP-143 sighash, P2WPKH witness,
   broadcast, and a funded customer branch. `primitives.py` already has every
   cryptographic part; what is missing is transaction construction. BTC's UTXO
   model is also the only one where the demo's float can be recycled with one
   signed transaction, which is why D9's gas objection does not apply to it.
3. **Ownership in the API**, enforced per endpoint, not per DocType.

## D12 · Splitting the rate lock into price-validity and acceptance — REJECTED, 2026-08-24

D11 measured the 15-minute lock as too short for testnet4 and named a longer
acceptance window as "the cheapest fix and the one that buys the most". That
fix was written down and attacked, and it lost.

The proposal: keep `price_valid_until` at 15 minutes for *making* a payment,
add a longer `accept_until` for *crediting* one already broadcast, derive it
from the rail's block time, credit at the rate locked at charge.

**Rejected. It does not create two windows; it creates one longer price lock.**

**The exploit, and it needs no bug.** Settlement checks the confirmation's
block time against the intent expiry and nothing else. It never establishes
when a transaction was first broadcast — and it cannot, because
`TransferObservation` carries `block_height` and `block_time_epoch` and **no
first-seen field** (reproduced, `plugin.py:205`). So:

1. 12:00, BTC at $100k. Open a $100 sale, receive a request for 100,000 sat.
2. Save the URI. Broadcast nothing.
3. 12:30, BTC at $50k. Broadcast the 100,000 sat now.
4. It confirms at 12:40, inside the proposed window. The sale settles and books
   $100 against crypto then worth $50. Had the price risen, abandon the quote.

That is a free put option written by the merchant, exercisable by anyone who
keeps a URI. Fresh-address attribution does not help: the payment is perfectly
attributable and previously unclaimed. Adding a first-seen time would not help
either — a low-fee RBF transaction broadcast at minute 14 can be bumped if the
price falls and replaced if it rises. A mempool transaction is not a payment.

**And the prices really do move, even here.** `rates.quote` reaches
`cryptopos_core.rates._gather`, which asks live feeds — so a testnet sale is
priced at the live mainnet market. The coins are valueless; the *quote* is not
fixed. This is the same root defect recorded against a crypto position report
in D6, arriving through a different door: **a test token priced at a real
market rate.**

**Three further claims, all reproduced.**

- `real_block_time` is prose (`"~10 min"`), not a policy number, and the
  persisted `Crypto Rail` row does not carry it at all — only
  `sim_block_seconds`. The derivation could not read the field it needed.
- The architect's own arithmetic was wrong: 4 x ~10 min is 40 minutes, not the
  80 claimed in the proposal.
- The longer window multiplies D11's starvation path. The heartbeat polls every
  in-flight sale sequentially; moving expiry from 15 to 80 minutes grows the
  steady-state unpaid population about 5.3x for the same attack rate.

**The trilemma that kills "nothing else moves".** At minute 15 the code must
either expire a sale whose honest payment the provider merely had not indexed
yet (terminal, and D10 forbids later settlement), or keep every sale alive to
minute 80 (making late first-broadcasts indistinguishable from timely ones), or
end the sale at 15 and settle it later (reopening a terminal state, violating
D10). There is no fourth option without a new durable commitment concept.

**What survives.** D11's measurement stands: 15 minutes is too short for a
chain whose median interval measured 20.0 minutes, and about a quarter of
honest sales fail. The problem is real; this fix is not the answer.

**Where an answer would have to come from.** The option is only free because
the price can move between quote and confirmation. Two directions, neither
taken yet:

1. **Quote a fixed operator rate for testnet.** A valueless token has no market
   price, so tracking one is dishonest as well as exploitable — this is the
   position already taken elsewhere in this workspace. A quote that cannot move
   makes the option worthless and lets the acceptance window be as long as the
   chain needs.
2. **Price at confirmation, not at charge.** Removes the option, at the cost of
   the customer not knowing what they will pay.

Both change what a sale *is*, which is why neither is a patch.

## D13 · A fixed operator quote for testnet — REJECTED, 2026-08-24

D12 left one route open: if the quote cannot move, its free option is worthless
and the acceptance window can be widened. That route was taken, written down,
and attacked. It lost, and the loss generalises past pricing entirely.

**"Fixed until the operator changes it" is not fixed for the life of an
outstanding quote — and that lifetime is exactly what the proposal lengthens.**
Open a thousand sales at $100/coin, wait for the operator to drop the rate to
$50, then pay the old URIs at one coin each. If the rate rises instead, abandon
them. The customer receives the best rate seen across the whole window. The
underlying changed from Coinbase to the operator's own configuration; the option
did not go away. `charge.py` freezes the native amount and expiry into the
intent, so no configuration change can reprice an outstanding URI.

The invariant actually required is: **the configured rate may never decrease
while any quote issued under the previous rate is still acceptable.** With a
long window, the operator can never safely lower it.

**And the crypto rate was never the only term frozen (both reproduced).**

- `loyalty_earn_rate` is snapshotted at charge and awards deliberately use the
  snapshot — `points_for`'s own docstring says the merchant "changes it
  freely". So a customer can open sales during a promotion, pay none, wait for
  the promotion to end, and then pay the old URIs to mint points at the expired
  rate. **The crypto quote never moved and the option was still there.**
- `settle.py` calls `frappe.get_single("CryptoPoS Settings")` *at settlement*
  and reads the current customer, item and company — not a charge-time
  snapshot. An old URI books into whatever configuration exists later.

An arbitrarily long quote is a standing unilateral authorisation to create
future accounting events under terms that did not exist when it was issued.

**Honesty was not established either.** `settle.py` creates a real
`Sales Invoice` with `currency = "USD"` for a testnet sale. Naming the source
`operator-fixed` makes the provenance accurate; it does not make the USD figure
true. D6 said converting faucet tokens into authoritative currency figures is
dishonest, and this proposal still does it — now with a deliberately invented
number. Making it honest means testnet sales stop booking real invoices, which
is not "nothing else changes".

---

### The result that outlives all three rejections

Block inclusion has **no finite upper bound**. Under D10, which never reopens a
terminal state, that leaves exactly three options and no fourth:

| choice | what it costs |
|---|---|
| a finite acceptance window | a nonzero tail of honest payments is always lost — D11 measured today's tail at ~25% |
| an unbounded window | perpetual obligations, unbounded watcher state, and every economically relevant term frozen forever |
| end the sale, settle it later | reopens a terminal outcome — violates D10 |

**This is a liveness trilemma, and no choice of numbers escapes it.** It is why
D11, D12 and D13 all failed at the same place from three different directions.

### The direction that survives it, and is not yet taken

D10 already builds the record of a late payment; what it does not do is *book*
it. But a new document is not a reopening. So:

**Let the reconciler book a late payment as a NEW sale, priced at the rate
current when it confirmed.**

- The old sale still expires on time and is never mutated — **D10 holds.**
- The new sale is priced at confirmation, so there is nothing to wait for and
  no term is frozen in advance — **D12 and D13's option disappears.**
- The customer's money is honoured rather than stranded — **D11's 25% closes.**
- The acceptance window stays short, so the in-flight population stays bounded
  — D11's starvation path is not amplified.

The cost is honest and worth stating: the customer sees their sale expire, and
then sees a second, separate booked sale appear. That is worse UX than a sale
that simply completes, and it is the first design in this series that does not
buy its comfort with somebody's money.

**Unchanged and still required before anything is public:** ownership in the
API (D11), admission quotas and poll throttles (D11, restated here), and
per-sale addresses or disabled rails on every rail that is enabled (D5, D9).

## D14 · Booking a late payment as a new sale — REJECTED, 2026-08-24

The one direction D13 left standing was attacked and lost. Four architectures in
one day, all trying to move the same boundary, all failing.

**A payment proves receipt. It does not prove agreement.** A per-sale address
shows that money reached an address allocated for the *old* intent. It does not
establish a new order, an accepted price, a delivered item, a customer, a tax
treatment, or which company earned the revenue — and `settle.book` asserts all
of those, from current settings, when it runs. So there are only two readings
and both refuse the design:

- If the payment satisfies the *original* purchase, it must carry the original
  item, customer, company and consideration. Repricing it and calling it a new
  document is an accounting correction of the old transaction — **a reopening in
  substance**, whatever the document identity says.
- If it does not, the merchant has received unapplied money. That is a deposit
  or a refundable receipt requiring a human decision — **which is exactly what
  D10 already does.**

**"Terms at confirmation" are not available.** The system is not running when a
retired address confirms, and there is no historical-price query —
`quote_detailed` fetches spot and stamps it `now()`. There is no immutable
history of the settings either. A transaction confirming at 09:59, an operator
changing company and item at 10:00, and an hourly sweep at 10:07 give the 10:07
terms. And a one-confirmation gate has no stable confirmation time at all: a
reorg can move the same txid into a later block at a different price, where
deduplicating by txid keeps the wrong price and reprocessing books twice.

**The option survives, it only changes shape.** A retired address becomes a
permanent bearer capability. Save addresses while loyalty is off; broadcast to
all of them when a high earn-rate promotion starts. Or wait until quotas, store
hours or inventory controls would refuse a new sale, and pay a retired address
instead — the automatic reconciler bypasses the admission path entirely.

**And the perpetual obligation does not go away, it relocates.** Reproduced:
`reconcile.py` bounds itself deliberately — `WINDOW_HOURS = 48`,
`sweep_late_payments(limit=25)`, `order_by="modified desc"`. More than 25
expiries an hour and older candidates are displaced by newer ones until they
leave the 48-hour window unchecked, forever. Remove the bound and every address
ever issued becomes a permanent automated commercial obligation. D11's
starvation path moved from `heartbeat` to `reconcile`; it did not close.

Six further implementation defects were named and the structural ones
reproduced: `look_again` returns only a **sum**, discarding txids, per-transfer
amounts and confirmation times; it counts unconfirmed transfers, so an RBF
payment could book an invoice and then be replaced; deduplication is one boolean
per old sale rather than per transaction, so one dust payment marks an address
done forever; and `tx_id` is indexed but not unique, so two sweep workers can
create two invoices for one transaction.

---

### What four rejections in one day actually establish

**D10's boundary was already in the right place.** A late payment is recorded
and handed to a human. Every attempt to automate past that — a longer window
(D12), a fixed quote (D13), a new document (D14) — failed, and each failed
because it needed the chain to supply a fact the chain does not have.

**So the goal has to change its promise, not its implementation.** "Any stranger
completes a sale end to end, unattended, reliably" is not available on
`bitcoin:testnet4` at a 15-minute lock: `make lockcheck` measures the loss at
~25% and D12–D14 close every route to recovering it automatically. The honest
options, none yet taken:

1. **Say the number on the screen.** Keep everything as it is and tell the
   visitor, before they pay, that a testnet4 block may not arrive in time and
   roughly how often that happens. A demo that is honest about a 20-minute chain
   is a better demonstration of this terminal than one that hides it.
2. **Demonstrate on a fast chain and fix attribution instead.** Sepolia's median
   interval measured **12.0 s**, and 0% of payments miss the lock. Its defect is
   D5's shared address, not timing — a different, and bounded, problem.
3. **Keep a human in the loop**, and make the late-payment queue a visible part
   of the demo rather than a failure of it.

Only the second is a route to an unattended demo, and it requires answering D5
and D9 on the EVM rails rather than avoiding them by choosing Bitcoin.

## D15 · A payment contract on Sepolia — REJECTED, 2026-08-24, and it corrects D14

D14 named one route to an unattended demo: move to a fast chain and fix
attribution with the payment contract D9 itself described. That was written down
and attacked. It lost, and in losing it **corrected a measurement recorded in
D14 and in `GOAL.md`.**

**It is not a payment contract; it is a custody vault with an attacker-controlled
memo field.** `pay(bytes32 invoiceId)` proves only that *a caller supplied these
bytes*. Nothing proves the terminal issued the ID, that it is open, that the
token and amount are right, or that the caller is entitled to pay it. The
terminal creates the amount and expiry in its database only. So:

- single-use IDs let an attacker consume an active invoice with one wei, and the
  real payment then reverts;
- repeatable IDs let them dust every open invoice, and every unpaid sale ends
  holding real partial money that needs review — **the unattended demo becomes
  an attended reconciliation queue**;
- enforcing terms on-chain requires either a per-invoice registration
  transaction (a signer in the terminal, which D9 forbids) or a merchant
  signature (an online signing key).

And this instance makes it easy: sale names are a predictable naming series and
`api.status` performs no permission check, so the IDs need not even be guessed
(D11 again). D5 is not removed — it is relocated into bearer-capability
security, where possession of the ID is authority over the sale.

**The watcher would reproduce D5 anyway.** The EVM observers match `to ==
recipient` and canonical ERC-20 `Transfer` logs; neither reads calldata or a
`Paid` event. Two one-ETH invoices, one `pay(invoiceB)` call, and invoice A's
observer still sees a one-ETH transaction to the shared contract. This needs a
new contract-specific adapter, not a changed recipient address.

**Transaction hashes stop being payment identities.** Claims are held by
transaction hash across every sale at the recipient; contract payments are *log
records*. One helper contract can pay two invoices in one transaction — same
hash, different log index — and whichever sale polls first claims it while the
other goes to `needs_review`. The durable identity has to become
`(chain, contract, txHash, logIndex)`, which changes `TransferObservation`, the
claim store, the receipt and reconciliation.

---

### The correction: "Sepolia is 0% stranded" measured the wrong gate

D14 concluded that Sepolia's timing is fine, on a measured median block interval
of **12.0 s** and 0% of payments missing the lock. That measurement is real and
it answers the wrong question.

Reproduced: `_finalized_tip` returns `None`, and `_is_mature` gates on **three
confirmations** — about 36 seconds. Three confirmations is not irreversibility.
The sequence: `Paid` enters block B, the gate passes at B+2, the terminal books
an ERPNext invoice, B is reorganised away, and the payer replaces the orphaned
transaction with the same nonce and no payment. **The canonical chain holds
neither the log nor the funds, and ERPNext says paid forever** — `may_book`
checks positive credited value and a transaction id, not that the block
survived.

Closing that means settling on finality. Until that is decided,
`tools/reorg_probe.py` at least *detects* it: for every sale carrying a
transaction id it asks the chain whether that transaction is still there and
still confirmed. Run against this instance on 2026-08-24 it checked 16 sales and
found none missing — so the defect is real and has not yet fired here. It
corrects nothing, because D10 says a terminal state is not edited afterwards. So the number that matters is
time-to-finality, and it is not 12 seconds. Measured live on Sepolia,
2026-08-24:

| gate | lag behind tip | against the 15-minute lock |
|---|---|---|
| 3 confirmations (what runs today) | ~36 s | fits — and is reorg-unsafe |
| `safe` (justified checkpoint) | **10.8 min** | fits, leaving ~4 min for the customer to act |
| `finalized` | **17.2 min** (14.0–18.6 across four samples) | **straddles it** |

**Two corrections to this table, both made later the same day.** Sepolia's
finality was sampled four times and ranged **14.0–18.6 min** — it *straddles*
the 15-minute lock rather than sitting past it, so "does not fit" was too
absolute. A gate that is sometimes inside the lock and sometimes outside is
still unusable for a demo, but for a different reason: it is unpredictable, not
uniformly slow. And **the generalisation below is false** — see D18, which
measures `polygon:amoy` finalizing in about two seconds.

**So every rail is either fast and unsafe, or safe and too slow.** testnet4 is
too slow at one confirmation (D11, ~25%). Sepolia is fast at three confirmations
and can permanently false-book; at finality it is slower than the lock. The
liveness trilemma of D13 was not a Bitcoin problem — Bitcoin was just where it
was noticed first.

**What this does to D14's options.** Option 2 — "demonstrate on a fast chain and
fix attribution instead" — was the only route to an unattended demo, and it is
not open as stated. What remains genuinely available:

1. **Say the number on the screen** (D14 option 1), unchanged and still honest.
2. **Keep a human in the loop** (D14 option 3), unchanged.
3. **Settle Sepolia on `safe` rather than on three confirmations**, and accept
   that a visitor has roughly four minutes of the lock to act. This is new, it
   is measured, and it is the only variant of option 2 the numbers still permit
   — but it needs the contract-authorisation problem above solved first, and
   that is a different architecture, not a configuration change.

**One more, easily missed:** `usdc-pol` is an Amoy rail. A contract deployed on
Sepolia cannot receive or identify its payments; that rail needs its own
deployment or must be switched off.

## D16 · The trilemma was wrong — three deadlines were being treated as one, 2026-08-24

D11–D15 concluded that an unattended public demo is not achievable here. That
negative conclusion was itself put under attack, and **it did not survive.**
Recording the overturn in full, because a wrong conclusion that five rejections
made feel proven is the most expensive kind.

**The smuggled premise.** Every rejected architecture assumed the payment
deadline, the finality deadline and the commercial quote deadline are one
instant. They are not, and *this codebase already knows it*: `bitcoin.py` tests
`block_time_epoch <= expires_at_epoch` (**was it committed in time**) entirely
separately from `confirmations >= 1` (**has it matured**), and `evm.py` does the
same. I read that split earlier and drew the wrong conclusion from it.

**The host throws the distinction away.** Reproduced, `watch.py`:

```python
if lock_expired and sale.state in ("awaiting", "detected", "confirming"):
```

**`confirming` is in that list.** A sale whose payment was already included in a
block *on time*, and which is merely waiting to mature, is made terminal at
wall-clock expiry. And `confirming` is explicitly non-terminal in `LEGAL`. So
there is a fourth option the trilemma missed:

> **A finite window for making an attributable commitment, followed by an
> unbounded wait for maturity — but only for sales that actually committed.**

This is not D12's free option, and the difference is exact: a saved URI with no
payment stays `awaiting` and still expires on time. The extended wait is only
ever granted to a payer who has *already transferred the invoiced amount*. That
is an ordinary merchant obligation, not an option. D10 is untouched — nothing
terminal is reopened, because the sale never became terminal.

**Where this actually bites, and where it does not.** Worked through here rather
than taken from the attack:

- **The EVM rails: decisive.** A Sepolia payment is included within ~12 s, so
  the sale reaches `confirming` long before expiry and then merely waits. D15's
  "finality is 17–19 min against a 15-minute lock" was **a category error** —
  finality does not have to arrive inside the quote window. Expiring those sales
  is pure, avoidable loss.
- **Bitcoin testnet4: does not help.** At a 1-confirmation gate, `confirming`
  barely exists — the block *is* the gate. A testnet4 payment is typically still
  in the mempool at expiry, so the sale is `detected`, and the block that
  eventually carries it fails the `timely` test anyway. Crediting it would need a
  trustworthy pre-expiry commitment, and a mempool sighting is not one: RBF lets
  it be replaced. **D11's ~25% stands for Bitcoin.** The trilemma was not wrong
  about testnet4; it was wrong about being universal.

**Two rails were dismissed without being looked at.**

- `PolygonAmoyUsdcRail` is seeded, enabled, and its own docstring says *"settled
  only once Heimdall reports the block finalized"* — it already settles on
  finality rather than on a confirmation count.
- `solana:devnet` and its SPL rail are in the core catalog as `RequestRail`s, and
  the sibling repository already has a working Solana payer. What is missing is
  an extracted observer — implementation work, not an impossibility.

---

### Measured on this instance: the dominant failure is not the one I was chasing

D16's claim was checked against the live database rather than argued. **No sale
here has ever entered `confirming`** — all 50 went `awaiting -> confirmed`
directly, because Bitcoin's gate is one confirmation and the block *is* the
gate. So the `confirming` expiry defect is **latent**: real in code, never fired
here. Said plainly, because "found in the code" and "cost us something" are
different claims.

What *has* fired is worse, and I was not looking at it:

| outcome | count | of 50 |
|---|---|---|
| `confirmed` — settled and booked | 16 | 32% |
| `expired`, `end_kind=clean` — nobody paid | 19 | 38% |
| **`needs_review`, `end_kind=unverified`** | **15** | **30%** |

**Fourteen of those fifteen carry one reason, verbatim:**

> *"The rate lock ran out and the last look never reached the chain, so the
> terminal cannot say whether this was paid."*

All fourteen on `btc`. Those sales were not lost to a slow block. They were lost
because **the terminal could not reach mempool.space at the moment the clock ran
out**, and a sale that cannot be looked at is being given a terminal state
anyway. That is D11's finding 4 — which I recorded as "accepted on mechanism,
not reproduced end to end" — **already reproduced, fourteen times, in this
instance's own data.**

`look_again`'s docstring says it deliberately treats "nobody looked" and
"nothing was there" as the same, and is explicitly right to: *"the distinction
matters inside a sale's lifetime, and this is after it."* At expiry the sale is
still inside its lifetime, and there the distinction is exactly what is being
thrown away.

**Corrected the same day, before acting on it.** The paragraph that stood here
called this "the single highest-value change" and said it "has already cost 28%
of every sale this instance has taken". Both claims were wrong, and checking
them before building anything is the only reason they are not now in the code.

- **`watch.py` already does what I was about to propose.** Its module docstring
  distinguishes the three answers explicitly — *"a heartbeat that fails is not a
  heartbeat that found nothing"* — and the code already retries a failed look
  for as long as the lock has time, ending `unverified` only when the clock is
  genuinely out. Those fourteen sales were handled **correctly by this module's
  own standard.** It did not lie; it declined to claim an observation it had not
  made.
- **The 28% is historical, from code that no longer exists.** All fourteen were
  charged on 2026-08-15, 08-16 and 08-17 — before the catalog rework of
  2026-08-23. Measured by day: 47 of the 50 sales predate that rework. **Since
  it, this instance has taken three sales**, and none reached this path. Three
  is not evidence of anything. Quoting 28% of an all-time total as though it
  described the current system was measuring the wrong population.

**What does survive, and it is smaller and real.** All fourteen recorded the
failure as `"final look did not reach the chain: "` — **with nothing after the
colon.** The exception stringified to empty, so the terminal has no record of
*why* the chain was unreachable: a rate limit, a DNS failure and a timeout are
now indistinguishable in the only place that remembers. That is the same defect
class as tender's order 0054, and it is the reason the root cause of those
fourteen cannot be diagnosed today.

**And the `confirming` gap in point 3 above is still real** — but it is latent,
not measured, because no sale here has ever entered that state.

**What was actually proved by D11–D15**, stated correctly:

> The current production-shaped USD settlement policy cannot be deployed
> unchanged as a reliable public demo.

That is true. *"The goal is not achievable"* is false, and D14's and D15's
closing sections overreach — read them with this entry beside them.

**The decision that remains is the maintainer's, and it is not an engineering one:** is
this a demonstration of the technical workflow, or a production-equivalent
commercial promise? The negative result depended entirely on assuming the
latter — the same 15-minute economic quote, the same real USD invoice, the same
mutable ERPNext settings, the same refusal of an online authorisation key. None
of that is in the goal. **If it is a demonstration, the unattended goal is
achievable**, and the first step is small: stop expiring `confirming` sales.

## D17 · An ending requires evidence, and `confirming` sales never expire — REJECTED, 2026-08-24

D16 ended with two proposals. Both were written up and attacked. Both lost, and
one of them was **dangerous**, which is why it is worth more than the six
rejections before it.

**Point 3 — "a `confirming` sale is not expired at all" — is an immortal-dust
vulnerability.** It assumed `confirming` means *"the full, attributable invoice
amount was included on time, and only maturity remains."* **It means nothing of
the kind.** Reproduced, and `_pending_state`'s own docstring gives it away — it
is *"for the screen"*, and *"derived from the observations rather than stored"*:

```python
best = max(batch.transfers, key=lambda transfer: transfer.confirmations)
if best.confirmed:
    return "confirming", f"mined, {best.confirmations}/{gate} confs"
```

It checks the *confirmed flag of the largest-confirmation transfer*. Not the
amount. Not attribution. Not timeliness. So:

1. Charge a BTC sale for 100,000 sat and let its lock expire.
2. Send **one satoshi** to its per-sale address and let it mine.
3. Settlement returns PENDING, because 1 sat is below the invoice — so
   `_pending_state` returns `confirming`.
4. With `confirming` removed from expiry, **the sale never ends**, and the
   heartbeat selects it forever.

**One satoshi per sale, permanently.** On the shared-address EVM rails one small
transfer can pin every overlapping sale at once, because underpayments never
enter the claimed-transaction set. That makes D11's starvation attack *cheaper*,
which is the opposite of the intent.

**And it needs no attacker.** Reproduced:
`LEGAL["confirming"] = {"confirmed", "expired", "failed", "needs_review"}` —
there is **no `confirming -> awaiting`**. A full payment observed in a block
moves the sale to `confirming`; a reorg removes that block; the next observation
finds nothing; and with expiry gone the sale is stranded in `confirming` with no
legal transition out.

**Point 1 — "an ending requires evidence" — is already implemented, and its
boundary is contaminated.** `watch.py` already distinguishes the three answers
and already retries a failed look while the lock has time (see D16's own
correction). What it wraps is `except Exception`, which catches settlement bugs,
pagination bugs, `_claimed_transaction_ids` database failures and ordinary
programming errors alongside transport failures. **An observation can succeed
and settlement then throw, and it is recorded as "did not reach the chain".** A
grace counter on that boundary retries deterministic defects four times and
files them as provider outages.

Nor is "the provider answered empty" conclusive: Bitcoin accepts any tip not
behind the baseline without establishing the provider is current, and the EVM
adapters have no mempool observation at all.

**Point 2 — the bounded grace — bounds neither time nor population.** A
heartbeat count is not elapsed time: `poll` is whitelisted and checks no
ownership (D11 finding 2, again), so anyone can burn the four retries in
seconds. With no admission quota the retained population is unbounded, and four
retries multiply provider pressure into a synchronised retry herd — **the grace
can reduce the chance any of its own observations succeeds.** Worse, a payer who
can induce the expiry observation to fail gains an outage-conditioned price
option, because timeliness is decided by a block header timestamp the payer may
influence.

**What a safe version would need**, and it is not a patch: a distinct state
meaning *fully paid, on time, awaiting maturity*, enforced as an invariant
rather than derived for display, plus a defined reorg path out of it.

### Correction to D16

D16 says the `confirming` gap is "still real ... but latent". **Amend that: it is
latent and acting on it as written would introduce a one-satoshi denial of
service.** The insight D16 recovered — that the payment deadline and the
maturity deadline are different — stands. The change it proposed does not.

`needs_review/unverified` is also not the terminal saying a sale went unpaid. It
is the state machine's explicit representation of *uncertainty*. The clock
decides when automation stops; it does not fabricate a conclusion. That is a
defensible design and I mistook it for a defect twice.

## D18 · Amoy is both fast and safe, and D15's central claim was wrong — 2026-08-24

D15 concluded, and D16 and GOAL.md repeated: *"every rail is either fast and
unsafe, or safe and too slow."* **That is false, and the counterexample was
already seeded, enabled and running on this instance.**

**Measured on `polygon:amoy`, 2026-08-24**, sampling `latest` against
`finalized` eight times over forty seconds:

| | |
|---|---|
| lag, `latest` − `finalized` | **0–2 blocks** |
| in seconds | **0–2 s** |
| `finalized` advanced over the sample | 38 blocks, 8 distinct heights |
| against a 15-minute rate lock | **fits, with 14m58s to spare** |

It is not pinned and not aliased to `latest` — it advances continuously, which
was checked precisely because a 0-second lag is what a dishonest endpoint would
also report.

**This is milestone finality, not checkpoint finality.** I assumed Polygon's
finality meant Heimdall checkpoints to Ethereum, which take tens of minutes.
Polygon's milestones are the fast path, and they settle in about two seconds.
Assuming the slow mechanism is why this rail was dismissed in D14 and D15
without ever being measured.

**And the adapter already gates on it.** Reproduced in `evm.py`:
`PolygonAmoyUsdcRail._finalized_tip` calls
`eth_getBlockByNumber("finalized", False)`, and `_is_mature` requires
`observations.finalized_tip`. Its docstring has said so all along — *"settled
only once Heimdall reports the block finalized."*

### What this does to the whole D11–D17 sequence

**The timing problem is already solved, on a rail that is switched on.**

- `bitcoin:testnet4` — 1 confirmation, median block **20.0 min**. Too slow
  (D11), and unrecoverable (D12–D14).
- `ethereum:sepolia` — 3 confirmations, ~36 s, **reorg can permanently
  false-book**; finality is **17–19 min**, past the lock (D15).
- **`polygon:amoy` — finalized in ~2 s. Fast, irreversible, and inside the
  lock.** No stranding, no reorg window, no change to `RATE_LOCK_SECONDS`, and
  none of D12/D13/D14's option problems arise because nothing waits.

So the liveness trilemma never applied to this rail. It is a property of slow
chains and of settling on confirmation counts, not of the terminal.

**What remains on Amoy is D5, and only D5:** it receives at a static address
shared with the two Sepolia rails (`rails_probe` reports it). That is the
bounded problem — per-sale addresses or a payment binding — and D9's gas
objection is the thing to answer, not a timing impossibility.

**And the obvious cheap fix does not work, which is worth writing down before
somebody tries it.** "Give each sale a unique amount and match on it" fails
against this adapter, because `EvmRail.settle` does not match a transfer to an
invoice — it **sums** every unclaimed timely transfer and settles when
`credited >= intent.amount_native`. Reproduced in `evm.py`. So with sales A
(10.00) and B (10.01) at one address, a 10.01 payment intended for B is seen by
A first, satisfies `>=`, and settles A; B is left holding nothing. Distinct
amounts do not survive an aggregating comparison. This is D5's own conclusion
arriving through the arithmetic rather than through the attack sequences:
**a shared address cannot be made safe by bookkeeping.**

### Correction to D14, D15, D16 and GOAL.md

Every one of them says or implies that no rail is both fast enough and safe
enough. **Read them with this entry beside them.** D15's measurement of Sepolia
stands; its generalisation to "every rail" did not survive being checked, and it
was never checked because the rail that disproves it was assumed rather than
measured.

The lesson is the one this register keeps relearning: **a claim about "every X"
earns its quantifier by measurement.** Two of the four rails here were measured
and two were assumed, and the conclusion was drawn from all four.

## D19 · The first genuine sale, and the nine-hour bug that had prevented every one before it — 2026-08-24

**A real sale completed end to end on this instance for the first time.**
`CPS-2026-00244`: charged on `eth`, paid from the bundled wallet, settled
against a real Sepolia transaction, booked into ERPNext.

```
state      confirmed / clean
credited   396000000000000 of 396000000000000 wei   (exact)
tx         0x8cba42e29ea86bbc4a72b86970c623a511743896357623632f4efd874bd6f935
invoice    ACC-SINV-2026-00052
events     charge -> confirmed (three-confirmation Sepolia gate) -> booked
```

Getting there took three attempts, and each failure was a real defect.

**1. The payer talked to the simulator.** `pay_cryptopos_sale.py` ran with
`app_state.mode` defaulting to `"demo"`, which has no endpoint ladder, so
`blockchain.call_node` routed to `simulator` and died on
`eth_getTransactionCount does not exist` — having signed nothing. The GUI never
met this because its mode switch is a real control. Fixed by having the script
declare the world it operates in, which is the same declaration the funding card
already makes.

**2. The second payment was genuinely late** — fifteen minutes passed while the
first defect was diagnosed. Recorded here because it is the only one of the
three that was *correct* behaviour: the sale reached `confirming` ("mined,
1/3 confs") and was then refused as arriving after expiry. D11's failure mode,
demonstrated live rather than argued.

**3. The one that matters: `expires_at_epoch` was nine hours in the past.**

`now_datetime()` returns a **naive** datetime in the **site's** timezone.
`datetime.timestamp()` reads a naive value as the **process's** local time.
Those agree only if the two timezones match, and here they do not — measured:

| | |
|---|---|
| site `time_zone` | `America/Adak` |
| container `TZ` | unset, so UTC |
| `now_datetime().timestamp()` | 1787549695 |
| `time.time()` | 1787582095 |
| **error** | **32400 s, exactly nine hours** |

Both adapters credit a transfer only when
`block_time_epoch <= expires_at_epoch`. With expiry nine hours behind the
present, **a payment made now was always "after expiry", on every rail.**

**Why nothing caught it, which is the part worth keeping.** The harness's
fixtures point at payments that are genuinely days old, and a days-old block
time compares perfectly well against an expiry nine hours in the past. So
**76/76 harness checks, 604 core tests, 120 render and 129 button checks were
all green while no live sale could settle at all.** Fifty sales had been taken
here and not one was a genuine end-to-end settlement — which I only noticed by
asking the database whether any settled sale carried a transaction id the
harness had not fabricated.

**Fixed** with `_epoch()` in `charge.py`, which attaches the site timezone
before converting. Verified to zero skew, then verified by the sale above.

### What this says about the rest of this register

D11 through D18 are twenty thousand words about why a sale might not settle. The
reason none of them settled was a timezone conversion, and **no amount of
attacking architectures would have found it** — it took charging a sale, paying
it, and watching what the terminal said. Codex was right eight times about
designs; the thing actually standing in the way was in neither the design nor
the adapters.

**The lesson, and it is not a new one here:** the suites proved the parts and
never the whole, because every fixture that could have exercised the whole used
data old enough to hide the defect. `make fit` and the probes now cover
deployment facts; **this one was a deployment fact too, and nothing was looking
at it.**

## D20 · The amount-bound invoice contract — PARTLY SURVIVES, 2026-08-24

The first proposal in this sequence to have a component survive. Attacked as:
an invoice id that is `keccak256(merchant, token, amount, deadline, salt)`, with
the contract recomputing the hash from the payer's arguments and pulling exactly
`amount` — so no merchant signature is needed, because every term is bound into
the id rather than attested by a signer.

**What survives.** *"The hash binding fixes the original D5 misattribution
attack."* Dust cannot consume an invoice (a wrong amount reverts), replay fails
on the used-id check, and reading an id off a screen buys nothing, because
paying it correctly means paying the full invoice. D15's objection — possession
of an id is authority — does not carry against terms bound into the id.

**What does not.**

- **Two-second finality is not "nothing waits".** Polygon documents milestone
  finality as typically 2–5 s, and a payment landing near the deadline can still
  be included-but-not-final when the deadline passes. The window is seconds
  rather than minutes, but D16's `confirming`-expiry problem applies to it — and
  D17 says that state cannot simply be exempted.
- **`transferFrom(amount)` does not prove the contract received `amount`.** A
  fee-on-transfer, rebasing or non-reverting-false token breaks the claim. The
  contract must measure its own balance before and after and emit the measured
  delta, not the requested one. Immutable token address is not sufficient on its
  own.

**So D5 is answerable on Amoy by binding, but the rail is not ready on binding
alone.** That is a materially better position than D9 or D15 left, and it is the
first time the answer to "what else breaks" has been a short list.

## D21 · D5 measured: the shared address does not lose money, it poisons the neighbours — 2026-08-24

D5 has been argued since it was written — seven attack sequences — and never
run. With D19's clock bug fixed, live sales settle, so it could finally be run.
Three real Sepolia payments, one shared address.

**Trial 1 — two concurrent sales, identical address, identical amount.**
`CPS-2026-00255` (A) and `CPS-2026-00256` (B) charged seconds apart, both for
396000000000000 wei to `0x611Ec5…D136`. **Only B was paid.**

> B settled correctly — credited 396000000000000, booked `ACC-SINV-2026-00055`.
> A did **not** take it.

**Trial 2 — an older open sale against a later payment.** A was still in flight.
`CPS-2026-00257` (C) was charged and paid.

> C settled correctly — booked `ACC-SINV-2026-00056`. A did **not** take it.

**So there was no theft, in either direction, and that is the claimed set doing
real work.** `_claimed_transaction_ids` reads `FOR UPDATE` and a transaction
bound by one intent cannot be credited to another.

**But look at what happened to A:**

```
state          needs_review
end_kind       unidentified
credited       0 of 396000000000000
sighted        0
review_reason  one or more observed transactions are already claimed by another intent
```

**A was never paid and never stole anything, and it still did not end cleanly.**
It ends in an operator-facing review state — not because money arrived that it
could not bind, but because money arrived *for somebody else* at the address it
was watching.

**That is the real cost of a shared address, and it is not the one D5
predicted.** D5 feared a payment credited to the wrong sale. What actually
happens is milder and broader: **every unpaid sale that overlaps a paid one is
collateral damage.** In a public demo with strangers charging concurrently, the
common case is not theft — it is that most sales end `NEEDS REVIEW` instead of
`EXPIRED`, and an operator is handed a queue of them.

**One structural fact reproduced alongside it.** `heartbeat` selects in-flight
sales with `frappe.get_all(..., pluck="name")` and **no `order_by`**, so which of
two simultaneously-pollable sales reaches a transaction first is decided by an
unspecified database ordering. Attribution held in both trials; nothing in the
code guarantees it will.

**And the review flood is not a defect in the state machine — do not "fix" it
there.** The branch is deliberate:

```python
if claimed and sum(t.amount_native for t in claimed) + sighted >= intent.amount_native:
    return SettlementDecision(NEEDS_REVIEW, ..., reason="... already claimed by another intent")
```

It fires when the transactions *claimed by other intents* would have covered
this invoice — that is, when there was enough money at this address to pay this
sale and something else took it. On a shared address, where the binding is
"static address + exact-amount match (weakest)", **that claim might be wrong,
and a human should look.** On a per-sale address it should be unreachable, and
if it ever fires there it is a genuine anomaly.

So the state machine is being honest about the weakness of the binding beneath
it. Expiring those sales cleanly would suppress exactly the signal that exists
because the binding is weak — the same shape of mistake D17 caught. **Fix the
binding and the flood disappears on its own.**

**What this changes.** D5's conclusion stands — a shared address cannot be made
safe — but the argument for fixing it is now the *demo experience*, not the
ledger. The answer is still D20's binding, and the reason to want it is that
without it a public instance produces review queues, not wrong invoices.

## D22 · D9's gas objection on Amoy, measured — the reason changes, the answer holds

D9 rejected per-sale EVM addresses partly on economics: *"a small sale can cost
more to collect than it contains."* On a testnet that sentence compares two
worthless quantities, so it was worth checking whether the objection survives on
`polygon:amoy` — the rail D18 showed is otherwise ready.

**Measured 2026-08-24:**

| | |
|---|---|
| Amoy gas price | 30.00 gwei |
| native POL send (21,000 gas) | 0.00063 POL |
| ERC-20 transfer (65,000 gas) | 0.00195 POL |
| merchant wallet holds | 0.018 POL |
| customer wallet holds | 0.080 POL |

**The economic form of the objection does not survive, and the operational form
does — with a number on it.** Collecting from a derived address costs **two**
transactions, because an EVM account cannot be an input to somebody else's
transaction the way a UTXO can: fund it with gas (0.00063), then transfer out
(0.00195). About **0.0026 POL per sale recycled**, and the merchant's current
balance covers roughly **seven** of them.

So the constraint is not "gas costs more than the sale is worth" — both are
valueless. It is that **gas is a finite, faucet-supplied resource, and per-sale
addresses consume two transactions of it per sale, forever.** A public demo that
recycles its float would drain the POL supply at a fixed rate per visitor and
need a human at the Polygon faucet to keep running. That is a worse failure than
a review queue: it stops.

**Which leaves the choice between the two answers to D5, with costs attached:**

| | per-sale addresses | the D20 binding |
|---|---|---|
| new artifact | none | a Solidity contract, a class of thing neither repo has |
| per-sale gas | **2 tx (~0.0026 POL)** | none — the payer pays their own gas |
| needs a sweeper | yes, off-terminal | one withdrawal for all sales |
| known outstanding defects | D9's twin-key and wrong-family bugs, already fixed once | must measure its own balance delta, not trust `transferFrom` (D20) |
| refuted by | — | not refuted; the binding survived attack |

**The binding is the better answer and it was already the surviving one.** What
this entry adds is that the alternative is not merely inelegant — it has a
measured running cost that a demo cannot pay indefinitely.

---

## D23 · The Amoy finality gate was checked against a stale tip — FIXED, 2026-08-24

Native POL was promoted from `RequestRail` to a fully capable rail today (D24),
and the first sale on it was paid, landed on chain, and **never credited**. The
sale sat `awaiting` with an empty `watch_scratch` until its lock ran out, then
ended `needs_review` saying *"the last look never reached the chain"* — the D19
diagnostic doing its job on a genuinely unreachable look.

**The look was not unreachable. It was self-contradictory:**

```
RailProviderError: Provider for rail 'polygon:amoy/native:pol' is not safe to
use: finalized block is above the latest block.
```

**The mechanism, reproduced.** `EthereumSepoliaRail.observe` read the chain tip
first, scanned for transfers second, and asked for the finalized tip **third** —
then compared that fresh finalized height against a `tip` captured before the
scan:

```python
tip = self._tip(provider)                                 # instant A
transfers = self._native_transfers(...)                   # minutes
finalized_tip = self._finalized_tip(provider, tip)        # instant B, vs A
```

`_native_transfers` costs **one `eth_getBlockByNumber` per block**. The sale's
baseline was ~975 blocks behind by the time it was polled, so the scan was ~975
sequential RPC calls. Amoy produces a block every ~2 s and finalizes 0–2 blocks
behind the tip (D18), so by instant B the finalized height was legitimately
*hundreds of blocks above* the tip from instant A. The guard fired every time.

**Why no rail had ever hit it.** The guard needs three things at once, and until
today no rail had all three:

| | needs | `eth` | `usdc-eth` | `usdc-pol` | `pol` |
|---|---|---|---|---|---|
| a finalized-tip gate | Amoy only | no | no | **yes** | **yes** |
| per-block scanning | native only | **yes** | no | no | **yes** |
| a fast chain | Amoy only | no | no | **yes** | **yes** |

`usdc-pol` has the gate on the fast chain but reads logs with **one**
`eth_getLogs`, leaving almost no window — which is exactly why it settled twice
today without complaint. `eth` scans per block but on 12-second Sepolia and
never calls the finalized gate. Native POL is the first rail to combine all
three, and it failed on its first payment, every time.

**The fix is ordering, not arithmetic.** `_finalized_tip` now runs beside the
`tip` it is checked against, before the scan. Both numbers describe one instant,
so the guard compares like with like. Reading it earlier can only make the gate
**more** conservative — an older finalized height matures fewer transfers, never
more — so this loosens no safety property.

**What it cost to find:** one real payment. `CPS-2026-00261` sent
0.084694 POL to the merchant address and ended `needs_review` unbooked. That
money is real, it is unapplied, and under D10 it stays that way until a human
reconciles it — which is the correct outcome for a sale the terminal could not
verify in time, and a fair price for the finding.

**What it says about the gates.** Every suite was green before and after. 604
tests, 100% line coverage and full mutation coverage did not see it, because it
is a race between two RPC calls whose window only opens on a chain no fixture
runs against. **The same shape as D19**: the parts were each correct, the whole
was not, and only a real payment on a real chain could tell the difference.
`prove_end_to_end.py --rail pol --send` is now the check that would catch it.

## D24 · Native POL is a real rail — TAKEN, 2026-08-24

`polygon:amoy/native:pol` was a `RequestRail` carrying the blocker *"the
provider-specific observer has not been extracted into this package"*, so the
terminal could build a POL payment request and could never see one arrive.

**Nothing needed extracting.** Native observation is the `token_contract=None`
path `EthereumSepoliaRail` already had, and the maturity gate is the Amoy
finalized-block rule `PolygonAmoyUsdcRail` already had. The rail was request-only
because nobody had composed the two halves, not because either half was missing.
The class was renamed `PolygonAmoyRail` — the finalized gate is a property of the
chain, not of the asset — and native POL is one line beside the USDC instance.

**Proven, not asserted.** `CPS-2026-00262` charged $0.01, was paid from the
bundled wallet, settled against a real Amoy transaction and booked
`ACC-SINV-2026-00060`, credited 83,297,000,000,000,000 of 83,297,000,000,000,000
wei exactly. It is the fifth rail this deployment can take money on.

**What this does not change.** POL receives at the same static address as the
other three EVM rails, so **D5 applies to it in full** and `rails_probe` now
reports four problems instead of three. It is not a step toward a public
instance; it is a step toward *any asset*, which is a different axis.

**And a caution that is not the rail's fault.** `rates.quote("POL")` returns the
**mainnet** price — 118,058 microcents on the day — so a testnet POL sale is
priced as if the token were real. That is D6's fourth rejection ("a crypto
position report... would value a faucet token at the mainnet price") showing up
in the charge path rather than in a report. A $1.00 sale wants 8.47 POL, which is
more POL than the bundled wallet holds; the proof above used $0.01 for that
reason.

---

## D25 · The stranding message told the Bitcoin operator the opposite of the truth — FIXED, 2026-08-24

The first real Bitcoin payment from the bundled payer stranded exactly as D11
predicted — `CPS-2026-00263`, 1,238 sat broadcast, sighted in the mempool,
`detected` for the full 15 minutes, no block, `needs_review`. That part is
working as designed and is not the finding.

**The finding is what it said:**

> 1238 arrived at this address inside the window but could not be tied to this
> sale. It is real money and **it is not provably this customer's payment**.

That sentence is false on this rail. The address was
`tb1q65rqjw60qk3gedqnq4rh3nrerrkmj4nghgr7ps` — derived from the merchant xpub
**for this sale and for no other** (D7). Money there is provably this customer's
payment; that is the entire reason per-sale derivation exists. What actually
happened is narrower: the transaction did not reach **1 confirmation** before the
rate lock expired.

**Why it was wrong.** `watch.py` hardcoded one sentence for both stranding
branches, and that sentence was written for the shared-address case, where it is
exactly right (D5). Bitcoin is the one rail in this deployment whose attribution
is sound, and it was being handed the shared-address apology.

**The damage is to the human, not the ledger.** No state, transition or amount
was wrong. But a review queue is read by a person deciding whether they may book
money, and this told them the strongest binding in the system was the weakest.
It would push an operator toward *not* booking a payment they are entitled to
book — or toward distrusting D7 and abandoning the one thing that works.

**The fix.** `_unbindable_reason(rail, sighted, gate)` follows the rail's actual
binding: a rail with a `testnet_xpub` says the money **is** this customer's and
names the gate it missed; a rail on a shared recipient keeps the original
wording, unchanged, because for it nothing better can honestly be said.

**Same family as D19 and D23.** Every suite green, no wrong number, and the only
thing defective was what the system said about itself. Three in one day is not a
coincidence: this codebase's remaining defects are concentrated in its
explanations, because those are the part no assertion checks.

---

## D26 · An operator's own asset can be charged — TAKEN, 2026-08-24

Codex argued that the `cryptopos.rails` entry-point registry is not an extension
point, and that "any asset" fails anyway because `charge()` calls
`_core_rails.rail_for(rail.rail_key)`, which knows twelve keys and raises a bare
`KeyError` for a thirteenth. **Both claims were reproduced.** The second took two
attempts: the first probe was refused earlier and more politely, by the endpoint
check, and only a fully configured rail with a price reached the `KeyError`.

**The wall was one line, and the data to get past it was already in the row.**
`invoice_amount` and `usd_cents_to_native` read exactly two fields —
`native_decimals` and `display_decimals` — and the `Crypto Rail` DocType carries
both. `install.py` seeds them from the frozen table, and all five enabled rails
were verified byte-identical between row and table before the switch, so nothing
about them changed.

It was also a split brain. Six lines below the `rail_for` call, the same function
already priced its own error message from `rail.unit_name` — the row. Two sources
of truth for one rail, and only one of them could describe a rail the operator
added.

`_scale_of(rail)` now reads the row, and asserts the one thing the DocType
cannot: that `display_decimals <= native_decimals`, because a display precision
finer than the chain's own asks for an amount no URI can state.

**Proven.** A disposable rail `zzz-probe` for a fabricated asset `ZZZ`, given a
demo price of $25.00, charged $1.00 to **0.040000 ZZZ** exactly, with a valid
Amoy URI and `rate_source = "demo-fixed (no feed answered)"`. Before this it was
`KeyError: 'zzz-probe'`. Harness 78/78 after, and `usdc-eth` re-proved end to end
(`CPS-2026-00285`) crediting the identical 1,000,030 as before.

**What is still required to add an asset**, and none of it is code:

1. a `Crypto Rail` row — gate in words, scales, recipient, endpoint;
2. a `catalog_key` naming an adapter that already exists (observation is still
   per-family code, exactly as D6 and Codex said);
3. a price — a live feed, or one entry in `rates.DEMO_MICROCENTS`, which can
   never price real money because `quote_detailed` refuses the fallback for
   real-money modes before reaching it.

**Codex reviewed this design cold afterwards and named a check this had
missed.** Trusting the row is only safe if the row agrees with the adapter that
will watch for the money. The frozen table could not be edited by an operator; a
DocType row can, and a row claiming 6 native decimals in front of an 18-decimal
adapter would invoice **a millionth** of the intended amount and settle it as
paid in full — with every arithmetic assertion in this codebase agreeing,
because they would all be reading the same wrong number.

`_scale_of(rail, adapter)` now refuses when `rail.native_decimals` disagrees with
`adapter.asset.decimals`. Reproduced both ways on a disposable rail: at 18 it
charges 0.040000 ZZZ, at 6 it refuses with *"Probe / ZZZ says 6 native decimals;
the polygon:amoy/native:pol adapter says 18."* Harness 78/78 with the check in.

This is the second time today Codex was right about something reproduced only on
the second attempt, and the pattern is worth naming: **its claims failed the
first probe because an earlier, politer guard fired first.** A finding that does
not reproduce immediately is not yet refuted.

**What this does not do.** It does not make an *observer* out of nothing: a rail
whose family has no adapter can be charged and never watched, which is why
`require_chargeable` still gates on the four capabilities. Extensibility here is
the charge path only.

## D27 · Exhausting POL took down two rails, not one — 2026-08-24

Two $0.01 native-POL sales consumed 96.5% of the payer's Amoy balance (D24), and
the next `usdc-pol` proof then failed before broadcasting:

```
customer balance 6,288,191,980,896,006 wei; worst-case need 16,527,615,277,120,000
NOT ENOUGH - fund the customer wallet first
```

**An ERC-20 transfer is paid for in the native coin.** So the rail that D18
identifies as the only one fit for a public instance — `usdc-pol`, ~2 s finality
— is down, and it is down because a *different* rail spent the gas. The USDC
balance is untouched at 18.99997.

This makes the mainnet-pricing question (D24) sharper than "one rail is
uneconomic". Native POL priced at $0.118 is a **drain on the shared gas budget of
every Amoy rail**, and the faucet refills roughly one sale at a time. The refusal
itself is honest and named the coupling, which is the only reason this was two
minutes of diagnosis rather than a mystery.

---

## D28 · The books-vs-chain check was reconciling a population somebody typed — FIXED, 2026-08-25

`tender-apps/apps/settled.py` is the only thing in this workspace that checks
**the books agree with the chain**, and it is the claim a point-of-sale has to
be able to make. It ran against `settled_fixture.json`, captured by hand in a
session, once.

**The arithmetic was never wrong. The population was.** The fixture's own header
said it held *"Every confirmed Crypto Sale on this ERPNext instance that carries
a real chain transaction"*. Queried live on 2026-08-25 the instance held **25**
such sales and the fixture held **23**. Missing: `CPS-2026-00285` (`usdc-eth`)
and `CPS-2026-00286` (`usdc-pol`) — both settled 2026-08-24 21:5x, both *before*
the fixture's own `captured_utc` of 2026-08-25T06:42:38Z, and both named in
`CONTINUE.md`'s proven-rails table. Read off the chains they agree exactly:

```
usdc-eth   booked=   1000030  chain=1000030  AGREE
usdc-pol   booked=   1000030  chain=1000030  AGREE
```

Nothing could have noticed. A reconciliation over a hand-picked set says the
sales in the set agree; it says nothing about the ones that were not typed in,
and it is silent in exactly the direction that matters — money the books hold
and nobody checked.

**Fixed as a probe, not as a better fixture.** `tools/settled_capture.py` reads
the books, `settled.py --capture` reads the chains, and the two are separate
processes on purpose:

```bash
cd sites && ../env/bin/python ../apps/cryptopos/tools/settled_capture.py > books.json
PYTHONPATH=site-packages python3 apps/settled.py --capture books.json
```

A single pass that read both sides and wrote both numbers would agree with
itself by construction and prove nothing. The probe opens no socket; the
reconciler never touches ERPNext.

**Result, 2026-08-25:** 25 of 25 sales match the chain exactly, across five
rails, in five different smallest units, with no USD anywhere.

**Two things the capture refuses rather than includes**, each because including
it would make a true-looking number: a confirmed sale whose `tx_id` is not
chain-shaped (a harness wrote it — a fixture that agrees is worse than one
that is missing), and a confirmed sale with a chain tx and no Sales Invoice
(settled and never booked, which is `reconcile.late_payments`' business and not
a books-vs-chain fact). Both are named in an `excluded` list rather than
dropped in silence.

**And the reconciler now grows with the deployment.** It took the exponent from
a table of five rail keys and would have `KeyError`d on an asset an operator
added — D26's whole point. It now reads `native_decimals` off the captured
`Crypto Rail` row, cross-checks it against its own belief for the five it knows
(a disagreement is a factor of ten thousand and looks fine), and for a rail it
has never heard of registers a code derived from the rail key that **starts
with a digit** — no real ticker does, which is what stops an operator's `pol`
row being registered as mainnet `POL` and quietly adding to it.

---

## D29 · The plugin path was a claim, not a capability — TAKEN, 2026-08-25

`cryptopos_core` has declared a `cryptopos.rails` entry-point group since it was
packaged. `catalog.plugins()` read `builtin_rails()` and nothing else, so the
group was decorative: an operator could install a rail wheel and the terminal
would never see it. "An operator can add an asset" (D26) was true only for an
asset one of the twelve built-in adapters already spoke.

`cryptopos/catalog.py` now discovers the group, caches the registry per process,
and **fails closed on a duplicate key**.

**Why refusal and not precedence.** `network.key/asset.key` names the concrete
money — one chain, one asset, one contract. Two adapters claiming it are not two
assets, so there is no correct winner:

- external-over-builtin makes behaviour depend on install order;
- builtin-over-external silently defeats an install the operator performed on
  purpose;
- namespacing by vendor is false identity — two implementations of the same
  Amoy token do not become two financial assets because their vendors differ.

**Three refusal shapes, all visible.** An entry point that fails to import, one
that loads something without a rail's shape, and one whose key is already taken
are each recorded with a reason. `plugin_for` distinguishes *nothing is
installed* from *something is installed and was refused* — those send an
operator to do opposite things — and `tools/rails_probe.py` prints the whole
registry, built-in and installed, with every refusal.

**Measured on the running instance:** 12 adapters, 12 built in, 0 installed,
0 refused. `cryptopos_core` is on the container's path rather than installed as
a distribution, so it advertises no entry points there — which is why the
identity check that makes a self-advertising builtin idempotent is written and
not yet exercised.

---

## D30 · An external plugin may not supersede a built-in — REJECTED, 2026-08-25

D29's registry refuses a duplicate `catalog_key`. That locks out the case the
plugin path exists for: `solana:devnet/native:sol` is a `RequestRail`
placeholder whose own blocker says *"the provider-specific observer has not
been extracted into this package"*, and a wheel that really does observe and
settle Solana devnet cannot claim the key because a stub admitting it cannot do
the job is sitting on it.

So a narrow exception was proposed: an external adapter may supersede a
**built-in that lacks OBSERVATION or SETTLEMENT**, if the external has all four
capabilities and exactly one external claims the key. Codex was asked to argue
against it. **It won, on four grounds, and one of them was a defect in the
discovery code rather than in the proposal.**

**1. The uniqueness condition is only true at one instant.** Install adapter A,
charge a sale under it, uninstall A, install B under the same key, restart
everything. Exactly one external claimant before and after. B then
reinterprets A's persisted baseline and can settle a payment A would have
refused, because `charge.py` stored the endpoint, the gate and the catalog key
and **no implementation identity at all**, and `watch.py` re-resolves the
adapter from the mutable rail row on every heartbeat. Verified by reading both.

**2. Four capabilities are not a settlement ABI.** A plugin can declare all
four, pass readiness and conformance, and still attribute by recipient and
amount while ignoring the payment reference. Two concurrent sales for the same
amount at the same address, the scheduler polls the unpaid one first, it
claims the paid one's transaction, and `_claimed_transaction_ids()` then locks
the customer who actually paid out of their own sale. Nothing in the contract
proves a plugin enforces the binding it advertises. The same hole lets a row go
on telling the operator *finalized* while the replacement settles at
*confirmed* — the row's gate is never passed to `adapter.settle()`.

**3. Mixed workers.** Frappe runs web, scheduler and two queues, and the
registry is cached per process. Restart the web workers only: a charge is taken
under the plugin, and every heartbeat resolves the same key to the cached
placeholder, gets `UnsupportedCapability`, and a fully paid sale reaches expiry
as `needs_review`. The flat refusal masks this class; the exception activates
it.

**4. The defect this argument found in D29's own code.** `_entry_point_rails()`
kept candidates in a dict keyed by `point.name`. Two distributions may choose
the same entry-point name, so one was dropped **before the collision check ever
ran**, and the survivor was whichever metadata iteration reached last.
Reproduced against the first draft:

```
candidates surviving: 1 -> collision check sees ONE of two wheels
winner is metadata iteration order: True
```

Fixed: candidates are keyed by origin — distribution name, version and entry
point — so every claimant reaches the check and the refusal names who already
holds the key.

**One correction to the attack, checked:** a `RequestRail` cannot itself have
settled sales, because `require_chargeable()` refuses it before a sale is
created. The dangerous records are in-flight or historical sales created by an
*earlier external adapter*, which is ground 1 and stands.

**Taken instead — the part of the attack that generalises.** Grounds 1 and 2
are not properties of the exception; they are properties of the whole plugin
path, and they would have shipped with D29. So `charge()` now stamps
`identity_extras["adapter"]` with the implementation that created the sale, and
`watch()` refuses to advance an in-flight sale whose implementation changed
underneath it:

> this sale was charged under X and this process is running Y. A different
> implementation of KEY must not settle an intent it did not create.

Sales charged before the stamp existed carry no `adapter` and are left alone —
stranding money over a field that did not exist would be a worse bug than the
one being fixed. App harness after the change: **78 passed, 0 failed.**

**What stays open.** The placeholder still holds a key no plugin may claim.
Resolving that is a source-level decision about `BUILTIN_RAILS` — deterministic,
made once, with no install-order dependence — and it is not this entry's to
take.

---

## D31 · A rail arrived as a wheel and took a real sale — TAKEN, 2026-08-25

The first rail this deployment ever gained without editing this app.
`pip install cryptopos-rail-solana`, one `Crypto Rail` row, and Solana devnet
went from *described* to *driveable*:

```
driveable: 6
solana driveable: True
identity: cryptopos-rail-solana 0.1.0 [solana-devnet-sol]
```

Then, end to end, with the bundled payer and real devnet money:

```
CPS-2026-00328  102000 lamport -> GyKqcxqdA7PbgbFXMW55G8rht5FhWPvgj9T96psdtZKc
binding reference ubGNWtvZLgKk2F66PVwLVPr6WhVxNFF1XTzh3MYdrEL
broadcast 4s8nk6WwiUDbM2DuFsVDHBQ2pyWbwm684TvY9DAjvbiCSCE18VsRLBP2PXKitCCXtbNu1uqjjqdKew98JL4yHPkG
PASS the sale settled -- confirmed
PASS it credited exactly what it invoiced -- credited 102000 of 102000
PASS it booked an ERPNext invoice -- ACC-SINV-2026-00075
```

**And it binds.** Solana Pay's `reference` is a fresh account the payer includes
on the transfer, derived here as `base58(sha256(intent_id))` — recomputed in
`observe`, never stored. Only this sale's money touches it. That puts `sol`
beside `btc` (D7) rather than beside the three EVM rails that still cannot
attribute (D5). It is the second rail here with a real payment binding, and the
first one that got it for free from the protocol.

### The first attempt failed with the money already spent, and the reason is a deployment fact nobody had written down

`prove_end_to_end.py --rail sol --send` broadcast a real payment and the sale
ended `needs_review`, credited 0:

> no payment intent on this sale: Rail sol names solana:devnet/native:sol,
> which this deployment knows about and cannot drive

**The containers do not share a Python environment.** `pip install` had been run
in `backend`, which is where `charge()` runs; the heartbeat runs in
`queue-short`, `queue-long` and `scheduler`, and all three answered
`PackageNotFoundError`. So the terminal could *sell* on a rail it could not
*watch*.

This is D30's third ground — mixed workers — reproduced with real money, and
worse than the version argued: it is not a stale in-process cache that a
restart fixes, it is four separate installations. The `catalog.py` docstring
already said *"a rail half the deployment can see is worse than one nobody
can"*. It was written as a caution and turned out to be a description.

**Nothing was lost and nothing was misbooked.** The refusal named the rail, the
key and the blocker, the sale went to `needs_review` rather than crediting
zero, and D10 kept it there. Installing into all four containers and restarting
made the same command pass on the next run.

**The operator procedure, therefore, is four installs and a restart** — not one.
A deployment that gains a rail in one process and not the others is the shape
this failure takes, and it will take it again for the next plugin.

### The oversight grew with it, and its own guard was wrong first

`tools/settled_capture.py` excluded the sale — *"tx id is not chain-shaped — a
harness wrote it"*. A Solana signature is 64 bytes of **base58**, 86–88
characters; the shape list held EVM hex and Bitcoin hex and nothing else. The
guard fired honestly and its rule was wrong, which is exactly the failure the
`excluded` list exists to make visible instead of silent — an enumerated list of
two chains' id shapes is a reconciler that stops covering the shop the moment
its operator adds a third.

With base58 added and a lamport reader in `settled.py`:

```
CPS-2026-00328  sol                     102000              102000  exact
26 of 26 sales match the chain exactly.
  sol          1 sales                  0.000102000 DEVSOL  (102,000 lamport)
```

### One defect in the plugin, and how it survived fourteen green tests

The build was dispatched to Codex against `ORDER-solana-rail-plugin.md`. It came
back with 14 passing tests, zero conformance issues, and
`DEVNET_GENESIS_HASH = "EtWTRABZaYq6iMfeYKouRu166VU2xqa1"` — the real hash
truncated at 32 of its 44 characters. `_verify_network` compares it to
`getGenesisHash`, so **the plugin would have refused every real devnet node as
not-devnet**, and every test passed because no test touches a node. Read off the
chain instead:

```
{"jsonrpc":"2.0","result":"EtWTRABZaYq6iMfeYKouRu166VU2xqa1wcaWoxPkrZBG","id":1}
```

Corrected, readiness against the live endpoint returns all four capabilities and
`chargeable: True`. Codex also ran a lint autofix across three files it had been
told not to touch; those edits were reverted.

---

## D32 · The refusals that had never fired — EXERCISED, 2026-08-25

D29 shipped three refusal branches and D30 added a fourth. Every one of them
was written, reviewed, and **never once executed**: the deployment had 12
adapters, 0 refused. That is the same shape as the truncated genesis hash in
D31 — a claim that is only true offline — so they were driven on purpose.

Driving them found a real asymmetry and one flawed test of my own.

**The asymmetry.** A *built-in* that cannot observe or settle is filed as
described rather than driveable. An *external* arriving with the same gap was
registered as an adapter anyway. Exercised:

```
3 an external claiming a DESCRIBED key with no capabilities:
    REFUSED halfrail 0.1 [h]: claims zcash:testnet/native:zec without being able
    to check the receiving address, watch for the payment, ...
```

Nothing was ever at risk of being mis-settled — `require_chargeable` refuses a
capability-less adapter at the counter. What was at risk was the **message**: the
impostor displaced the described entry, so the operator lost the blocker
explaining why that money cannot be taken and gained a rail refused for reasons
it no longer stated. Same class as D25. Externals now clear the same bar as
built-ins, because it is the same question.

**The two collision refusals, driven:**

```
1 two externals claiming one key:
    REFUSED beta 2.0 [b]: claims x:y/native:z, which is already provided by alpha 1.0 [a]
2 an external claiming a DRIVEABLE builtin:
    REFUSED impostor 9.9 [i]: claims bitcoin:testnet4/native:btc, which is already provided by builtin
```

**D30's identity guard, driven.** A real sale was charged on `sol`, its
`identity_extras["adapter"]` stamp was edited to a different implementation, and
one heartbeat run — `CPS-2026-00339`, deliberately never paid:

> no payment intent on this sale: RuntimeError: this sale was charged under
> some-other-rail 0.0.1 [x] and this process is running cryptopos-rail-solana
> 0.1.0 [solana-devnet-sol]

**And the capture's shape rule, driven** — the branch that wrongly excluded the
first Solana sale now classifies all three:

```
solana signature   -> solana
harness placeholder -> None (excluded, and named)
real evm tx        -> evm
```

**One test of mine was wrong before the code was.** The first attempt at the
duplicate check stubbed out entry-point discovery, so the impostor claimed a key
nothing else held and was — correctly — accepted. The refusal had not failed;
the scenario had not been built. Worth recording because it is the failure mode
of testing a guard: it is easy to write a case the guard was never meant to
catch and read the pass as evidence.

---

## D33 · D31's binding claim was false as implemented — CORRECTED, 2026-08-25

D31 said the Solana rail "binds a payment to a sale" with "no ambiguity", and
`tools/rails_probe.py` was changed to stop reporting it as a D5 shared address.
**That was wrong, and it was wrong in the direction this workspace treats as
worst: a surface telling an operator the opposite of the truth.**

Codex was asked to attack the plugin after it had already taken real money. It
returned nine findings and claimed to have executed several. The central one
was reproduced here before anything was changed.

### The finding: a reference is a search key, not proof

`getSignaturesForAddress` returns every transaction whose account list
**mentions** the address. It does not say which instruction used it or what
that instruction moved — and Solana Pay permits several references on one
transfer. The rail decoded no instructions: it checked that the recipient and
the reference each appeared once in `accountKeys`, then credited the
recipient's whole transaction-wide balance delta.

Reproduced against the implementation — one 100-lamport transfer whose account
list names two sales' references:

```
  A (invoiced 60): sees 100 lamports -> settled
  B (invoiced 40): sees 100 lamports -> settled
  B once A has claimed that signature: needs-review
```

One payment, two invoices booked. Whichever polls first wins and the other goes
to review. That is a race deciding which sale steals the money, not attribution.

### Fixed by reading the instruction, not the balance

Credit now comes only from `SystemInstruction::Transfer` instructions — data
decoded from base58, discriminant 2, little-endian u64 — whose `accounts[1]` is
the recipient and whose account list carries this sale's reference **and no
other extra account**. The multi-reference case is refused outright rather than
attributed:

```
THE ATTACK: one transfer, both references on the SAME instruction
  A (invoiced 60): credited 0 -> needs-review
  B (invoiced 40): credited 0 -> needs-review
THE HONEST SHAPE still works:
  A (invoiced 60): credited 60 -> settled
SPLIT PAYMENT across two referenced transfers still sums:
  A (invoiced 60): credited 60 -> settled
```

An unreferenced instruction paying the same merchant in the same transaction is
no longer credited: 102 became 60, which is the half that is ours.

The rail also refuses when the instructions claim more arrived than the
recipient's balance moved. Picking the larger number is how an inconsistent or
lying node gets believed.

**Verified against the chain, not only the fixtures.** `getTransaction` for the
transfer that proved this rail returns account keys
`[payer, merchant, reference, system program]` and one instruction over accounts
`[0, 1, 2]` — exactly the shape now required — and parses to `(102000, ...)`.
Re-proved live afterwards: `CPS-2026-00351` → `ACC-SINV-2026-00081`.

### Two more that lose or misstate money, both fixed

**The baseline discarded a payment landing in the slot it was read at.**
`ObservationBatch` requires a transfer to sit strictly above the baseline, so
recording the current confirmed slot excluded any payment in that same slot —
permanently, because nothing looks below the baseline again. `capture_baseline`
now records the last slot already behind the sale.

**A knowingly incomplete history still settled.** The signature walk stops at a
10,000 safety limit and says so; `settle` checked `credited >= invoiced` before
looking at that warning, so it wrote a credit it already knew was a lower bound
into a state D10 says can never be reopened. It now goes to review. That is
provokable by spamming the reference — a denial of service rather than a theft,
and the better of the two things to hand an attacker.

### The fixtures agreed with the bug because they came from the same idea

The package's transactions carried **no instructions and no signatures at all**
and settled anyway, and the recipient was
`11111111111111111111111111111111` — the System Program. A fixture whose
recipient is not a wallet cannot represent a payment. Both corrected; the
account list now matches what devnet actually serves. 14 tests became 20, six of
them locking down findings that all passed before the fix.

### And a fourth, fixed after the first three

**A pruned node was read as an empty chain.** The rail never called
`minimumLedgerSlot`. A node prunes; if its retained history advances past the
slot a payment landed in, `getSignaturesForAddress` returns `[]` — which is
indistinguishable from *nobody has paid yet*. The sale expired cleanly while the
money sat finalized on the chain, and nothing anywhere said why.

One call answers it, and devnet answers it: `minimumLedgerSlot` → `486277834`
against a tip near `487863000`, roughly 1.6M slots retained. When that minimum is
above the sale's baseline the rail now says so as a history warning — which the
fix above turns into an operator decision rather than a silent expiry.

### Accepted rather than fixed

- **`blockTime: null` sends a finalized payment to review.** The rail cannot
  date it, and the sale has an expiry. Crediting a payment it cannot place in
  time would be worse than a human decision, and the message names both
  possibilities.
- **One endpoint is trusted.** True, and true of every rail here — `eth` and
  `pol` read one JSON-RPC each. Not a property of this plugin.
- **Address lookup tables are refused, not decoded.** Stated in the README.

### What the claim should have said

The binding is real **now**, because the reference is checked on the instruction
that paid. It was not real when D31 was written, and `binds_per_sale = True` was
true of the design and false of the code for about an hour. The lesson is the one
this repository keeps relearning: **a property is not a property until something
that could disprove it has been run.**

---

## D34 · Two tools against the one thing a model cannot do — BUILT, 2026-08-25

the maintainer's observation, and it is correct: an agent cannot generate long opaque
identifiers reliably. This session produced three instances in a few hours, each
of which survived every offline check:

| string | what it really was |
|---|---|
| `EtWTRABZaYq6iMfeYKouRu166VU2xqa1` | the devnet genesis hash, 12 characters short. 14 unit tests passed; the rail would have refused every real node |
| `11111111111111111111111111111111` | the Solana **System Program**, used as a merchant address in fixtures |
| `"1" * 87` | a made-up signature. Base58 `1` is a leading zero byte, so it decodes to nothing; devnet answered `Invalid param: WrongSize` |

The common shape is that **a wrong identifier does not look wrong**, and nothing
in a test suite compares it to anything real. Reading harder does not help.

**`tools/idcheck.py`** — offline, no keys. Given any string it reports the shape,
**how many bytes it actually carries**, whether a checksum holds, and whether it
is a well-known constant somebody has mistaken for their own. A short byte count
is what truncation looks like and it is stated in those words.

Two things it deliberately does not do. It does not keep its own checksums —
addresses go to `cryptopos_core.addresses.validate`, because a second copy of a
checksum is a second thing that can be wrong on its own. And it does not report
`UNCHECKED` per string: Solana, Tari and Ootle publish no local checksum, so
their validators accept anything, and a line saying "unchecked for xtm" beneath
a truncated hash reads as a hint that it might be a Tari address. That fact is
**derived with a control string** and printed once as a footer, so the day one of
those chains grows a checksum the footer changes itself.

**`tools/snapshot.py`** — the deployment in about twelve lines: adapters
driveable, installed and refused; which rails bind per sale and which are
SHARED; sale counts by state; money that settled and never booked; and the three
most recent settled sales with their real ids and transaction hashes. It replaces
four `frappe` heredocs that answered the same questions at forty lines each.

Printing real ids is the point rather than a convenience: the tool exists so the
next session **copies** an identifier instead of typing one.

---

## D35 · Three cold reviewers on one session's defects, and what they found still live — 2026-08-25

the maintainer asked for the session to be reviewed by agents that could not see it, for
**patterns of reasoning-instead-of-checking**. A dossier of 17 defects was
written (`SESSION-2026-08-25-DEFECTS.md`) and three Codex agents worked from it
in parallel: extract the pattern, attack the remedies, hunt live instances.

Every finding below was reproduced here before anything was changed.

### The pattern is not one thing

Nine distinct mechanisms wearing one costume. The ones that earned their keep:

- **Opaque-literal fabrication** — long identifiers cannot be produced from
  memory, and a wrong one looks exactly like a right one.
- **Closed-world logic inside an extensible system** — enumerating today's
  inventory in a build whose stated goal is that operators add assets.
- **Property laundering** — a design intention becoming a flag, a docstring or
  an operator message without a counterexample ever having tried to falsify it.
- **A test whose antecedent is false** — the guard is invoked with the condition
  it guards against removed, and green means only that some code ran.
- **Single-process reasoning about a multi-process runtime.**
- **Duplicated semantics with no shared oracle** — two components answering one
  question, so fixing one leaves the other wrong.

### The remedies were attacked and lost

`idcheck.py` and `snapshot.py` were charged with covering **3 of 17** defects,
and with being instances of the pattern themselves. Reproduced, all true:

| claim | check | verdict |
|---|---|---|
| `snapshot.py` omits `idle` from the state list | schema says 8 states, tool had 7 | **true** — an idle sale vanished from the breakdown *and the total* |
| its "bookable" is a one-term approximation | `may_book()` is a five-term gate | **true** — a simulated sale would read as bookable |
| `idcheck.py` discards `UNCHECKED` | a lowercase EVM address returns *"carries no EIP-55 checksum and a typo in it cannot be detected"* | **true** — the tool dropped the one message that says "we cannot tell" |
| its footer describes the wrong layer | `validate("xtr", junk)` is UNCHECKED; the Ootle **adapter** REFUSES the same text | **true** — it answered about the legacy validator and printed it as a fact about the terminal |
| it always exits 0 | — | **true** — it could not gate anything |

Both tools now derive rather than approximate: states from the doctype schema,
booking from `may_book()`, rail keys from `rails.RAILS`, blanket-unchecked
reasons from a control string. `idcheck` exits non-zero on anything not whole
and names the layer it queried. `snapshot` says **THIS PROCESS ONLY** and
prints `per-sale(claimed)` where the binding is an adapter's assertion rather
than a derived address.

### What was still live, and cost the most

**1. The legacy Solana watcher had D33's defect, untouched.** `watchers.py`
called the reference *"collision-proof binding, so every signature here is this
sale's money"* and never looked at which instruction used it. Reproduced:

```
ref-A -> 100
ref-B -> 100
```

One 100-lamport transfer, two sales, both credited in full. **The plugin was
fixed this morning and this copy was not, because the fix was reasoned about as
"the rail" rather than searched for as "every implementation of the rule".**
Fixed, and the false sentence in the comment corrected.

**2. The simulator could not represent a real payment.** Its fabricated Solana
transaction had **no instructions at all** — so once the watcher started reading
them, every demo sale on `sol` stopped settling. The fixture had never been the
shape a node returns; nothing noticed while nothing read it. Same defect as the
plugin's original test fixtures. Now emits the shape devnet actually serves.

**3. The reconciler skipped an unknown rail and reported success.** A rail with
no reader printed `SKIPPED`, left the denominator, and the run ended
`0 of 0 sales match the chain exactly` followed by `settled: ok`. Oversight
claiming success while checking none of a new rail's money — in a system whose
goal is that operators add rails. It now refuses to report success and names
what it could not check.

**4. The reconciler's own copy of D33 was incomplete.** Written *to apply D33*,
it required the reference to appear on the instruction and did not reject a
**second** reference, then fell back to the balance delta. Same transfer,
`100` for both sales. The rule and its copy drifted apart inside the change that
created them.

### The structural fix: shared examples, not shared code

`packages/cryptopos-rail-solana/tests/attribution_vectors.json` — ten vectors,
four of them **real devnet transactions** read off the chain, six hostile, each
with the amount it actually paid. Both implementations run over them:
the plugin's own suite, and `tools/attribution_agreement.py`, which loads the
`tender-apps` reconciler by path and asserts the two agree with the record **and
with each other**.

Not shared code — a reconciliation that shares an implementation with the thing
it reconciles proves nothing. Shared *examples*.

**It found an eleventh defect on its first run**, which neither reviewer nor
author had seen: where the instructions claim more than the balance moved, the
rail refuses and the reconciler returned 60.

```
FAIL  instructions claim more than the balance moved
      rail None · reconciler 60 · expected None
      THEY DISAGREE — one transaction, two answers
```

Fixed; now 10/10 agree.

### And the constants nothing was checking

`make check` — lint, four Pythons, 100% executed, full mutation coverage —
cannot tell you that `USDC_ON_AMOY` is USDC. Every test naming it compares the
constant to itself; the EIP-55 test catches a typo and passes a wrong-but-valid
address. `tools/constcheck.py` now derives what can be derived (the ERC-20
`Transfer` topic *is* keccak-256 of its signature, and this repository ships
keccak-256 — it never needed remembering) and fetches the rest: contract code,
`symbol()`, `decimals()` against the rail table, the devnet mint's decimals, and
the genesis hash. **9/9 pass**, which is the first time any of it was
established rather than assumed.

### The habit the tools cannot replace

From the pattern agent, and it is the part worth keeping:

> Copy opaque facts from authoritative output. Run topology checks before money.
> Run the affected gate immediately after edits and reverts. Do not state a money
> property until a hostile counterexample has failed. When semantics change,
> search for independent consumers before declaring the fix complete.

`prove_end_to_end.py` now enforces the second of those itself: it refuses to
spend if the four Frappe workers do not agree about which rails they drive.
Verified by uninstalling the plugin from `queue-long` and watching it refuse.

---

## D36 · Per-sale EVM addresses attacked again on mainnet economics — REJECTED, and the economic leg was the wrong leg, 2026-08-28

D22 rejected per-sale EVM addresses with a reason that is entirely about a
faucet: *"gas is a finite, faucet-supplied resource... A public demo that
recycles its float would drain the POL supply at a fixed rate per visitor and
need a human at the Polygon faucet to keep running."* The long-horizon goal is
not a demo, it is a self-hosted business on real chains, where gas is bought.
So the position was put to Codex to attack:

> D22's rejection is a TESTNET-ECONOMICS argument that does not survive the
> move to mainnet. At the live POL price the two-transaction recycle cost D22
> itself measured — ~0.0026 POL — is about **$0.0014 per sale**, a rounding
> error against any real ticket and an order of magnitude under any card
> processor's fee. Meanwhile D20 needs a Solidity contract: a class of artifact
> neither repo has, deployed and audited per chain. For a self-hosted operator
> the dominant cost is artifact count, not gas.

**It lost, and the reason is worth more than the answer.**

> *"The position is wrong because it mistakes payment attribution for a complete
> receiving system. Delete D22's faucet argument entirely and D9 still wins."*

The economic objection was the leg I attacked and the architectural one was the
leg holding the weight. D9 never rested on gas: *"What a real EVM answer would
need: ... a complete account-management system — global allocation, permanent
branch monitoring, verified derivation metadata, signer, gas station, sweeper,
dust accounting. Address generation alone is not the feature."* Making gas free
does not supply one item on that list.

**Every citation in the counter-argument was checked here, and all six are
accurate.** That is not the usual outcome and is recorded because it is not:

| claim | where | verified |
|---|---|---|
| the library holds no keys and does not sign, sweep or pay out — adding that changes the threat model | `packages/cryptopos-core/SECURITY.md` | verbatim |
| D9's requirement is an account-management system, not addresses | `DECISIONS.md` D9 | verbatim |
| money paid past the gap limit is money the operator's own wallet will not find | `cryptopos/catalog.py`, `GAP_LIMIT = 20` | verbatim |
| the sweep looks back 48 hours and reports an unanswering endpoint as zero found | `cryptopos/reconcile.py`, `WINDOW_HOURS = 48` | verbatim |
| address allocation holds a row lock across a price-feed call, so one slow feed blocks every concurrent charge | `DECISIONS.md` D11 | verbatim, and already reproduced there |
| D20 must measure its own balance delta rather than trust `transferFrom` | `DECISIONS.md` D20 | verbatim |

**The three failure modes that decide it**, none of which gas can pay for:

1. **A successful sale can produce revenue the business cannot spend.** A
   derived address holding only USDC has no native coin. Collecting it needs
   that address's private key, a signer, a gas station, and nonce replacement.
   Put the extended private key on the ERPNext host and a Frappe, plugin,
   backup or web-process compromise is a treasury compromise. Keep it off-host
   and the "artifact-free" option has just grown a signer service, a sweep
   queue, a gas station and a recovery database — more artifacts than the one
   contract it was meant to avoid.
2. **A restore can silently lose money that was paid.** Address 0 is paid; bots
   and abandoned checkouts allocate 1–20 without paying; an honest customer pays
   21; the allocation metadata is lost. A restored wallet stops at the gap and
   never reaches 21. This repository already documents that exact gap, in the
   file that allocates.
3. **An address binds identity, not agreement.** An EOA accepts 99 USDC against
   a 100 USDC invoice, 101, the wrong token, native coin sent to a token
   address, and any payment after expiry — unconditionally, forever, because an
   account has no deadline. Per-sale addressing says *which sale* acquired the
   problem. D20 binds merchant, token, amount, deadline and salt into the id, so
   the wrong amount **reverts** and the money is never acquired.

**The conclusion for the stated goal, and it is not the one the costs suggest.**
D20 is the answer to D5 on the EVM rails, and the reason is not that it is
cheaper — it is that **it needs no merchant signer at payment time.** That is
the only shape compatible with the sentence in `SECURITY.md` that defines what
this library is. Per-sale addresses are not a cheaper way to reach the same
place; they convert a read-only receiving library into a hot-wallet custody
system, which is a different product with a different threat model.

**The immutability argument was backwards, too.** Once an address has been
shown, its derivation path, key and monitoring obligation are permanent, and a
derivation defect cannot be upgraded away from money already sent there. A
contract needs no proxy: deploy V2, route new invoices to it, keep V1 for its
existing state.

**What was NOT measured, and should be before anyone relies on failure mode 2.**
The live count of consecutive unused Bitcoin addresses on this deployment — the
rail that *already* allocates per sale under D7. The stack was down, so the
number is unknown here. `GAP_LIMIT` is 20 and abandoned checkouts consume the
run; whether this instance is near it is a database query, not an argument.

**Not re-fought, and this entry is the reason not to.** D9, D22 and now D36 are
three separate attacks on the same option from three different angles —
correctness, economics, and custody. It has lost all three.

---

## D37 · Role-gating the oversight endpoints "while we wait for step 1" — REJECTED, and the boundary is not where it was being looked for, 2026-08-28

Measured statically over `cryptopos/api.py`: eleven `@frappe.whitelist()`
endpoints, **seven with no `frappe.only_for` at all**, and **none** constraining
anything to the calling user. Four of the seven return instance-wide *financial*
data — `unbooked`, `settled_not_in_ledger_count`, `settled_not_in_ledger_usd`,
`late_payments` — and both of the query functions behind them use
`frappe.get_all`, which bypasses row permissions by design.

That looked like ordinary safety work that `GOAL.md` step 1 does not gate, since
no answer to "one shared shop or a shop each" makes the shop's takings public.
The position put to Codex was that `frappe.only_for(["System Manager"])` could
therefore be added today, matching `claim_awards` and `report_award`.

**It lost, and the refutation is more useful than the change would have been.**

> *"'Instance-wide' does not imply 'System-Manager-only'. An aggregate inherits
> the authorization scope of its input rows."*

In a shop-each model the correct aggregate is `SUM(unbooked belonging to this
shop)`. Denying every shop operator so only the instance administrator can sum
all shops **is** an answer to step 1 — a centrally administered installation in
which shop operators get no financial oversight — smuggled into four decorators.

**The concrete breakage, and every link was checked here:**

| claim | verified |
|---|---|
| `Sales User` is the role the terminal requires, and the visitor probe assigns it | `tools/isolation_probe.py` |
| `charge` / `poll` accept `Sales User` | `api.py` |
| `Sales User` already has **read, report, create, write** on `Crypto Sale` | `crypto_sale.json` permissions |
| `Crypto Takings` authorises `Sales User` outright | `crypto_takings.json` roles |
| both oversight number cards are `is_public=1` and call these exact endpoints | `settled_not_in_ledger_{count,value}.json` |
| the workspace carrying them is `public=1` with **no** role restriction | `workspace/cryptopos.json` |

So adding the guard would have **broken the operator's own oversight surface** —
the cashier loses the red count and the USD warning, and the late-payment queue,
in exactly the state those cards exist to detect (a settled sale whose invoice
failed) — **while changing no confidentiality at all**, because the same user
can still read every row from the `Crypto Sale` list and run Crypto Takings.
The worst available outcome: the summary breaks, the data does not move.

**The sharpening, and this is the part to keep.** D11 records that *"owner-based
DocType rules alone cannot fix this"*. The complement is now established:
**API-level role guards alone cannot fix it either.** The confidentiality
boundary is not in `api.py`. `Sales User` reaches the same money three ways —
the endpoint, the DocType list, and the report — and a guard on any one of them
moves nothing. Step 4 is therefore not "add `only_for` to seven endpoints"; it
is one decision (**who is the operator principal, and what scope does a shop
have**) applied at three surfaces at once. That is genuinely step 1's question,
and the fence is correct.

**What is safe to do before step 1, and was done:** measure. `tools/api_surface.py`
reads the surface statically — no bench, no socket — and fails while any endpoint
exposing another party's data has no owner constraint. It enforces nothing and
proposes no model; it turns step 4's scope from an adjective into a list.

**Two architecture calls were attacked today and both of mine lost** (D36, D37).
Both times the position was reasonable and the leg it rested on was the wrong
one. The habit is the finding: neither loss cost anything except the argument,
and D37's refutation would have been a live regression in a running deployment.

---

## D38 · `reorg_probe` cannot tell "the chain says it is gone" from "I got no answer", and has been failing on Solana since the sixth rail landed — 2026-08-28

`GOAL.md` records this gate as **PASS — none missing**. It is not. Run against
the live stack today it reported between five and nine sales with "no live
transaction behind them", and **the set changed on every run**:

```
run 1: 00244 00260 00285 00328 00350 00351 00352      (7)
run 2:             00257 00328 00350 00351 00352      (5)
run 3: 00244 00256 00257 00285 00328 00350 00351 00352 (8)
```

A reorg is not intermittent. Two different defects are hiding in one number.

**1. Every Solana sale is a permanent false positive, and it is a probe bug.**
`00328`, `00350`, `00351`, `00352` are flagged in all three runs. The probe
branches on `transport == "esplora-rest"` and sends **everything else** to
`_evm_confirmed`. `sol`'s transport is `json-rpc` — the same string the EVM
rails use — so a Solana signature is asked of `api.devnet.solana.com` with an
Ethereum method. Asked both ways:

```
eth_getTransactionByHash  ->  {"error":{"code":-32601,"message":"Method not found"}}
getTransaction            ->  FOUND — slot 487871523, blockTime 1787671388, err=None
```

The money is exactly where the ledger says it is. **This is D33/D35 for the
third time**: the sixth rail arrived as a wheel (D31) and an independent
consumer was never taught about it, because the fix was reasoned about as "the
rail" rather than searched for as *every implementation of the rule*. Two
consumers were found and repaired in that pass; this third one was not, and
nothing compared them because `reorg_probe` needs a live stack and never runs
in a suite.

**2. The EVM sales drift in and out, because a silent endpoint reads as a
missing transaction.** `00244`, `00256`, `00257`, `00260`, `00285` appear and
disappear between consecutive runs with nothing on any chain changing. The
`except` arm correctly reports *"could not ask the chain"* and skips — but that
only catches a **raised** failure. A JSON-RPC error object, or a null result
from a public node that pruned or simply declined, returns `known=False`, and
the probe prints **"unknown to the node"** — wording that asserts the node
answered.

**The defect is one sentence: `missing` conflates three states.** Reorged away,
pruned, and *nobody answered* are operationally opposite — the first is a
correction a human must make, the third is "run it again" — and they share a
counter and a line of output. This repository already knows the shape:
`reconcile.py` says in its own docstring that an endpoint which does not answer
is reported as zero found, *because* "nobody looked" and "nothing was there" are
the same to a sweep that runs again in an hour. There the collapse is deliberate
and written down; here it is neither, and it fails in the alarming direction —
it tells an operator that booked money has no transaction behind it.

**What this cost to find:** bringing the stack up and running the probe three
times. One run looks like a finding about the chain. Three runs make it a
finding about the probe. **Nothing in any suite could have caught it** — the
probe needs a live bench, so it is outside `make check` by construction, and its
own output has no notion of a control.

**Not fixed in this entry.** The repair is a Solana branch plus a third state
(`unreachable`, distinct from `confirmed` and `gone`) that is reported and
excluded from `missing`. Until then, `GOAL.md`'s gate row is corrected to say
what the probe actually reports and why the number moves.

---

## D39 · A rail can be agreed by all four workers and unreachable by all of them — 2026-08-28

Found while verifying D38's fix, and it is the more serious of the two.

`erpnext-hr/rails_agree.sh` **passes**. All four Frappe processes drive the same
six rails and the Solana plugin is installed in every one of them. `snapshot.py`
lists `sol` as enabled. It has settled real sales and booked real invoices.

**And no container can reach the chain.** From inside `backend`:

```
api.devnet.solana.com                  -> FAILS x4  (Temporary failure in name resolution)
api.mainnet-beta.solana.com            -> 208.115.249.134
solana-rpc.publicnode.com              -> 104.20.24.117
ethereum-sepolia-rpc.publicnode.com    -> 172.66.150.162
polygon-amoy-bor-rpc.publicnode.com    -> 104.20.24.117
mempool.space                          -> 103.165.192.204
api.coinbase.com / google.com          -> resolve
```

From the **host**, the same name resolves fine (`208.115.212.49`). It is one
hostname, inside the containers, persistently — four attempts, four failures,
while a sibling `solana.com` name resolves on every try. It settled sales on
2026-08-25, so this is a change, not a configuration that never worked.

**What it would cost.** A sale charged on `sol` today is broadcast and never
observed: it ends `needs_review` with the money gone. **That is the D31 incident
exactly** — the one whose lesson produced `rails_agree.sh`.

**And `rails_agree.sh` cannot see it.** It asks whether the four processes
*agree about which rails they drive*. It never asks whether any of them can
*reach* the chain. Those are different questions, and today they have different
answers. The gate built from D31 answers the question D31 happened to raise
rather than the one it was really about: **can this process actually settle this
rail right now.**

This is also why `reorg_probe` reports the four Solana sales `UNREACHABLE` after
D38's fix rather than `CONFIRMED`. That classification is **correct** — the
probe is telling the truth about what it could learn — and chasing it as a probe
bug would have been chasing the symptom. The money is fine: asked from the host,
`CPS-2026-00352` is on devnet at slot 487871523 with `err=None`.

**Not diagnosed here, and deliberately not worked around.** Whether the cause is
Docker's embedded resolver, an upstream DNS change, or something in the compose
network is a deployment question. Pointing the rail row at
`solana-rpc.publicnode.com` — which *does* resolve in the container — would make
the symptom disappear without anyone understanding it, and would silently move
the deployment onto a different node's view of the chain. **That is the maintainer's call.**

**The gate:** `tools/reach_probe.py` — for every enabled rail, from the process
it runs in, make a real read-only call to that rail's configured endpoint and
exit non-zero if any cannot be reached. Run it in all four containers, for the
same reason `rails_agree.sh` is run in all four.

### D39, continued · the blast radius is wider than the rail — measured 2026-08-28

`reach_probe` landed and was run in all four containers. It confirmed D39 above
and then showed something the DNS test alone had not:

```
backend       sol UNREACHABLE                        1 of 6 unreachable
queue-short   sol UNREACHABLE + usdc-eth UNREACHABLE 2 of 6
queue-long    sol UNREACHABLE + eth      UNREACHABLE 2 of 6
scheduler     sol UNREACHABLE                        1 of 6
```

Sepolia failed in two of the four processes, in a back-to-back sweep, with
nothing wrong at Sepolia. **It does not reproduce in isolation:** running the
same probe three times in `queue-short` alone reports exactly one unreachable
rail — `sol` — every time.

The mechanism, measured:

```
ethereum-sepolia-rpc.publicnode.com    6/6 ok, slowest 0.1s
polygon-amoy-bor-rpc.publicnode.com    6/6 ok, slowest 0.0s
api.devnet.solana.com                  0/6,  EIGHT SECONDS per attempt
```

Every lookup of the unresolvable host **hangs for eight seconds** before
failing, and the containers share Docker's embedded resolver. Four processes
each burning 8 s on the same dead name transiently starves it for everything
else.

**So one unresolvable hostname does not merely disable its own rail — it
intermittently takes the other rails down with it.** A rail that cannot be
reached is a contained problem; a rail that degrades the resolver is not, and
this is a deployment whose whole watcher layer is DNS-dependent. It also means a
`needs_review` on `eth` or `usdc-pol` may have nothing to do with Ethereum or
Polygon.

**Two things follow, and neither is mine to decide.** Repointing `sol` at a host
that resolves would fix both symptoms at once — and would still be moving the
deployment onto a different node's view of the chain without anyone
understanding the original cause. Disabling the rail removes the starvation
without pretending the rail works. Both are the maintainer's.

**What is not in doubt:** `sol` cannot be watched from this deployment right
now, and `rails_agree.sh` says everything is fine.

---

## D40 · The spend guard now has a third door, and the remedy for D39 was confirmed without taking it — 2026-08-28

`prove_end_to_end.py` is the only check here that spends real money. It already
refused for two reasons: the four workers disagree about which rails they drive
(D31), and the payer cannot fund the rail (it calls `runway.capacity`, the same
function the report calls, deliberately not a second copy). D39 showed a third
door standing open — **agreement is not reachability** — so it now runs
`reach_probe` in all four containers for the rail about to be charged, and
refuses before anything is broadcast.

It reuses the probe rather than reimplementing the check, for the reason this
register keeps recording: two implementations of one question is how D33, D35
and D38 happened.

**It had to ask the containers, not the host.** That is the whole content of
D39 — `api.devnet.solana.com` resolves on the host and not in the containers, so
a host-side reachability check would have passed and been worthless.

**Three live checks, run here because Codex's sandbox has no Docker and it said
so rather than claiming otherwise:**

| check | result |
|---|---|
| `--rail sol` (dry) | **REFUSES**, exit 1, all four workers report the rail unreachable |
| `--rail eth` (dry) | passes, all four REACHABLE, proceeds to describe what it would charge |
| same rail, endpoint overridden **in memory** to `solana-rpc.publicnode.com` | **REACHABLE from all four** |

The third is the control: the same rail, with nothing changed but the endpoint,
in a process that writes nothing — so the guard is driven by reachability and
not by something incidental to `sol`.

**And it answers D39's open remedy without taking it.** Repointing the rail row
*would* work; that is now measured rather than assumed. It is still not done,
because it relocates the deployment onto a different node's view of the chain
and buries the cause. **What was gained is that the maintainer now chooses between two
remedies whose outcomes are both known, instead of between a known and a guess.**

**One defect found in the guard by reading its own output**, and it is this
repository's signature shape. On the refusal path it printed a second line:

```
sol  UNREACHABLE — probe process failure: <frozen site>:101: RuntimeWarning: ...
```

No process failed. The container prints two harmless `RuntimeWarning`s on every
invocation, and the branch fires whenever the probe legitimately exits non-zero
*and* anything is on stderr. The condition is true and the sentence is false —
D25 and D38 again — and it appears **only** when a rail is genuinely unreachable,
which is exactly when someone is reading carefully. It sends them after a Python
environment problem while the finding is a hostname that does not resolve.

### D40, continued · the false message is fixed, and the distinction now holds both ways

Verified live here, all three cases:

| case | result |
|---|---|
| `--rail sol` — genuinely unreachable | four honest `DNS failure` lines, **no** "process failure", refuses, exit 1 |
| container name pointed at something that does not exist | `probe process failure: No such container` — still reported as one, refuses |
| `--rail eth` — healthy | all four `REACHABLE`, proceeds, exit 0 |

The rule the fix encodes: **a non-zero exit from the probe is the probe
answering, not the probe failing.** "Process failure" is now reserved for the
case where it could not run or produced no usable answer, and other stderr
surfaces as `probe diagnostic (stderr)` rather than as a second verdict — so a
diagnostic can never again masquerade as a cause.

Worth keeping as the general form, because this register has now recorded it
four times (D25, D38, D39, D40): **when a guard prints a reason, ask what else
satisfies the condition it printed it from.** Every one of these defects was a
true condition attached to a false sentence, and all four were invisible to
every suite.

---

## D41 · `reorg_probe` answers block *identity*, not block existence — and the header cache it was meant to copy was rejected — 2026-08-29

D38 left the repair unwritten: "a Solana branch plus a third state
(`unreachable`, distinct from `confirmed` and `gone`)". Both landed. This entry
is what came after, when the probe was asked a harder question: *is every booked
sale still backed by the chain?*

**The proposal was Tari's.** `minotari_payment_processor`'s
`confirmation_checker` keeps the last 2000 block headers as
`(height, header_hash, prev_hash)`, compares each new tip's `prev_hash` to the
stored tip, walks back to a common ancestor on mismatch, and reverts the
affected batches. the maintainer asked for that design. Codex was asked to attack it and
**the attack won.** Three of its findings were reproduced here before anything
was built:

> "A cache keyed only by rail silently compares those views and labels their
> difference a reorg… Calling the result 'the chain' would repeat D35's property
> laundering. It is an endpoint-observation log."

> "The empty cache stores `B102` as the transaction's first-seen placement and
> reports `CONFIRMED`. The re-mine has been permanently erased from the probe's
> history… A detector whose evidence disappears on routine deployment is not an
> audit trail."

That is D39's own reasoning — an endpoint is one node's view, not the chain —
plus this repository's rule that a check must not derive its expectation from
the thing that can break. And the four containers have four filesystems (D31),
so four caches would hold four histories of one sale.

**What survives from the Tari design is the part that needs no cache.** Compare
a transaction's containing-block identity against the canonical block at that
height, freshly, every run:

| rail | identity | canonical comparison |
|---|---|---|
| EVM | receipt `blockNumber` / `blockHash` | `eth_getBlockByNumber(n).hash`, plus depth and `finalized` membership |
| bitcoin | `/tx/{id}/status` `block_height` / `block_hash` | `/block-height/{h}`, plus `tip - height + 1` |
| solana | `getTransaction.slot` | `confirmationStatus == finalized`; **no invented slot depth** |

A `status: 0x1` receipt whose `blockHash` is not the canonical hash at its
height read CONFIRMED before. It reads REMINED now.

**A defect D38 never named.** `watch.py:55-63` says a settlement can credit
several transactions, stores them all in `watch_scratch`'s `settled_tx_ids`, and
calls `tx_id` merely "the one a human quotes". The probe read only `tx_id`. So
did `tools/settled_capture.py`, and that is noted in its source rather than
fixed. Measured on this instance: 29 sales carry a `tx_id`, **0 are
multi-transaction today**, and 16 have no `settled_tx_ids` at all because they
predate the convention — so it is a blind spot, not a live false positive, and
an absent scratch must fall back while a *corrupt* one must refuse.

**Then the finished implementation was attacked, and seven findings survived
reproduction.** The two that mattered:

*A run that proves nothing exited green.* 29 sales, 7 unanswered, and it printed
"no usable chain answer says a sale is under-backed" and exited 0. Keeping
UNREACHABLE out of GONE is right and is D38; exiting 0 having proved the claim
for 22 of 29 is not. **An unproven universal is not true.** The exit code is now
three-valued: 0 proven, 1 actionable, 2 inconclusive.

*The Solana SHALLOW branch was dead code.* Reproduced live on devnet:

```
processed slot 489729498, finalized 489729468, gap 30 slots
getTransaction (no commitment)        -> null
getTransaction (commitment=confirmed) -> OBJECT
getSignatureStatuses                  -> "confirmed"
```

`getTransaction` defaults to *finalized* commitment, so a confirmed-but-not-yet-
finalized transaction returns null, becomes UNREACHABLE, and never reaches
`getSignatureStatuses`. Combined with the first finding, that sale read green.
The harness passed because its fixture supplied an answer the real API would not
give. **This is D19's shape: a green check over a path that cannot execute.**

The other five: the probe reimplemented `gate_for(mode)` and `endpoint_for(mode)`
which the DocType already owns and `charge.py`, `watch.py` and `catalog.py`
already use — a fourth copy of one rule, so a mainnet sale would have been
checked against the testnet chain; the population filter was `tx_id is set`
rather than booked, hiding booked sales with no `tx_id`; GONE was outranked by
REMINED, so a 404 hid behind a hash mismatch; a corrupt `watch_scratch` fell
back silently; and the EVM receipt was never checked to be the receipt asked
for.

**The structural change is that this is no longer outside `make check`.** D38
said the probe "needs a live bench, so it is outside `make check` by
construction", and that is exactly why the Solana bug survived. The logic now
lives in `tools/reorg_probe_core.py` with an injected transport and no `frappe`
import at module level; `tools/h_reorg_probe.py` drives it from recorded
answers with no socket and no bench; and `make reorg` is part of `make check`.
**30 checks, 14 mutation modes, every one seen red.** Three defects injected
*outside* the mutation switches were each caught — nulling `_evm_canonical_hash`,
truncating `settled_transaction_ids`, adding UNREACHABLE to `ACTIONABLE_STATES`
— so the harness is not self-confirming.

**Persistence survives in one honest form.** `--journal PATH`, off by default,
appends one observation per (sale, transaction) and **decides nothing**. Proven
live: tampering a journalled block hash printed
`RE-MINED (journal evidence) … observation-vs-observation … live state remains
BACKED`, with REMINED still 0 and exit still 0. The journal records; it never
supplies an expectation.

**Two judgment calls, resolved rather than left open.**

`SHALLOW` counts toward the exit status. The probe takes its threshold from
`rail.gate_for(mode)` — the *same* function settlement uses at `watch.py:253` —
so a booked sale reading SHALLOW means its depth fell below the gate that
authorised the booking. That can only happen through a reorg. It is actionable.

**A repeat, and where it belongs (2026-08-30).** A transport-level retry was
built first and **never fired**, because the failure this probe actually suffers
is `{"result": null}` — a successful HTTP read the transport never sees. The
repeat now wraps the whole classification, which is where silence becomes a
verdict, and it is bounded, counted, and printed: *"endpoint health: 5 read(s)
needed a second attempt. The verdicts above stand; this counts the endpoint, not
the chain."* It can only improve UNREACHABLE — GONE, REMINED, SHALLOW and BACKED
are answers and are returned the first time they are given, which is pinned by
four checks. On the run that measured it the repeats did **not** rescue the
count: 12 UNREACHABLE before and after, 5 second attempts, all still silent.
That is the honest outcome and the reason the counter is printed.

**Where it stands live:** 29 sales, 17 BACKED, 0 SHALLOW, 0 REMINED, 0 GONE,
12 UNREACHABLE, exit 2. Four of the twelve are `sol`, unreachable inside the
containers for D39's reason and not this probe's. See D38, D39, D15, D33, D35.

---

## D42 · The EVM rails can bind a payment to a sale — 2026-08-29

D5 ended with an order: *"Per-sale addresses for them need BIP-44 + keccak,
which `hd.py` does not have; that is the next order on this line."* This is that
order, and the reason it was taken now is that a counter-argument said so.

**The question that produced it.** The proposal on the table was node quorum —
settlement facts decided by several independent endpoints instead of one. Codex
was asked to attack it and rejected it outright:

> "Single-node trust is not the largest live safety hole. Payment attribution
> is. The current EVM rails can unanimously and honestly settle the wrong sale;
> quorum would merely give that mistake multiple signatures."

Reproduced against the code before accepting it. `settle()` credits every
timely, unclaimed transfer to the recipient, and the baseline is a block height,
not a binding. So a transaction broadcast for sale A and confirmed after A
expired is unclaimed, timely and sufficient for sale B — whose customer sent
nothing. **Every honest node reports that identically**, which is exactly why
more nodes buy nothing.

**What was built.** `hd.evm_address()` takes an account xpub child to an EIP-55
address: uncompressed point, x and y as 32 bytes each, keccak256, last 20 bytes.
`cryptopos/catalog.py` picks the address builder by family from
`_ADDRESS_BUILDERS`, and `DERIVING_FAMILIES` is derived from that same dict so
the two cannot drift. The row lock that serialises allocation, the index
advance and the refusals were already family-agnostic — `btc` had paid for the
hard part in D7.

**One implementation of the checksum rule.** `addresses.to_eip55()` is shared by
`hd.evm_address` and the existing `_check_evm` verifier. D33 and D35 are both
"one rule, several implementations, one of them got fixed".

**Guards, each of which refuses rather than guesses.** An EVM rail refuses
tpub/zpub/vpub and a Bitcoin rail refuses mainnet xpub bytes — SLIP-132 assigns
zpub/vpub to Bitcoin P2WPKH, and deriving an EVM address from them would send
money nowhere. The key must be account-level at depth 3. And two rails may not
share one account key, because they would derive the same address from the same
index and show two customers one address.

**Verified independently, not from the builder's report.** `keccak256` against
three published vectors. The EIP-55 encoder against four vectors fetched from
the EIP-55 page *and* against an encoder written separately from the spec's
wording. The derivation against a secp256k1 decompression written from the curve
equation rather than reusing `hd.py`'s point maths — five distinct addresses
from BIP-32 test vector 1, deterministic, every one passing the library's own
validator. The wrong-family guard against the repository's real harness key: it
still derives `tb1qjmalnk7asntx02x2r3e30x0p7h3rsc2rs9hvrg` on the Bitcoin path
and `evm_address` refuses it. `make check` green — 100% line coverage,
2096/2107 mutants killed, 3.9/3.11/3.13/3.14. App harness **85/85**, up from 78.

> **The first attempt to test the version-byte guard was worthless.** A `vpub`
> was invented for it and died at the checksum, so nothing was exercised. It is
> the workspace's own rule — a model cannot produce a long base58 string
> reliably, and a wrong one does not look wrong. Retested with the real key.

**THE DEPLOYMENT IS STILL SHARED.** All four EVM rails carry a
`testnet_recipient` and no xpub, so nothing changes until an operator configures
an account xpub they control. That is not free: money then lands across many
addresses, and `sweep_evm.py`, `harnesses/live_funds.py`, `wallets.py`,
`gui.py` and `pos_actions.py` all assume a single merchant address. They were
listed and deliberately not changed — the back-office half is its own order.

See D5, D7, D20, D33, D35.

---

## D43 · A required field on a published plugin protocol is a breaking change — 2026-08-29

`api.rails` computed the operator-facing binding as `"per-sale" if derives else
"shared"`, where `derives` meant only "this rail has an xpub". So one row said
both things at once:

```
{'name': 'sol', 'binding': 'shared',
 'maturity_note': "... Binding: Solana Pay reference -- only this sale's money
                   touches it."}
```

The app was inferring a property the library already declared, and inferring it
wrong — D33/D35/D38's pattern, and the surface D33 calls the worst kind: one
telling an operator the opposite of the truth. The fix was to have the library
declare a *category* and the app read it.

**Making that field required broke every already-installed plugin.** The app
harness, 82/82 an hour earlier, died:

```
Rail sol names solana:devnet/native:sol, which this deployment knows about and
cannot drive ... It can be described and it cannot be charged.
```

Confirmed inside the backend container:

```
installed from: .../site-packages/cryptopos_rail_solana/__init__.py
version: 0.1.0
declares binding_category: False
```

A rail that has settled real money and booked real invoices became undriveable,
and per D31 it would have done so in all four Python environments.

**Nothing caught it, and the reason is the point.** The unit suite was green
throughout — 615/615 core, 23/23 plugin, 100% line coverage, full mutation —
because it exercises the plugin **source in this repository**. The deployment
imports the **installed wheel**. This is D31's incident caused by a protocol
change rather than a missed install, and D19's shape again: every suite green
while the deployment cannot take a payment. It was found by running the live
harness after a green `make check`, which is the only thing that could have
found it.

**The rule.** A published plugin protocol may not gain a required field. Make it
optional with a **pessimistic** default, so absence is both backward compatible
and fails in the safe direction. `binding_category_for()` uses
`getattr(rail, "binding_category", NOT_UNCONDITIONAL)`: an undeclared binding is
reported weaker than it may be, because understating a binding is conservative
while overstating one tells an operator a payment is bound to a sale when it may
not be.

`catalog.declared_binding_category()` then resolves in an order worth keeping:
an explicit plugin value is authoritative; an older plugin with no field
inherits the matching **built-in concrete rail's** declaration, matched by
catalog key and never by an editable row name; otherwise the pessimistic default
stands.

**The control needs no injection.** The installed 0.1.0 plugin still declares
nothing, still drives, and reads `not-unconditional`. And the check that the
category is read rather than inferred was seen red: reinstating the old
derivation-only rule turns the harness to *"FAIL Solana reports its
reference-bound payments as per-sale"*, 84 passed 1 failed.

See D31, D19, D33, D35, D38.

---

## D44 · Sepolia cannot be made reorg-safe under the rate lock — measured, 2026-08-29

D15 records that `eth` and `usdc-eth` settle at three confirmations and can
therefore be false-booked by a reorg. The obvious remedy is the one the Amoy
subclass in the same file already uses: settle only at or below the `finalized`
block tag, which cannot be rolled back. `EthereumSepoliaRail._is_mature` is
`observations.tip - transfer.block_height + 1 >= 3`; `PolygonAmoyRail._is_mature`
is `transfer.block_height <= observations.finalized_tip`. The safe mechanism is
already written, already tested, and already running on the neighbouring rail.

**It cannot be applied to Sepolia.** Measured against the live chains:

```
sepolia  tip=11593688 finalized=11593606  lag = 82 blocks = 1020s = 17.0 min
amoy     tip=46211099 finalized=46211098  lag =  1 block  =    1s
```

`RATE_LOCK_SECONDS` is 15 minutes. Moving Sepolia to the finalized tag puts
settlement **beyond the lock**, which is D11's failure mode exactly: an honest,
immediately-paid sale fails permanently because the gate is slower than the
window. D12 already attacked and rejected simply making the lock longer — it is
not two windows, it is one longer price lock, and settlement cannot tell an
early broadcast from a late one.

**So the three-confirmation exposure on the two Sepolia rails is structural.**
It is a property of that chain against this product's timing, not an oversight
in the adapter, and no amount of care in `_is_mature` removes it. `GOAL.md`
already carried a finality figure of 14.0–18.6 minutes for `ethereum:sepolia`;
this measurement lands inside it and turns a range into a decision.

**What it means for a business rather than a demo.** Polygon-class rails can be
both fast and final — Amoy pays *one second* for a gate that cannot be reorged,
which is why it takes the safe rule for free and why D18 was able to overturn
the timing conclusion of D11–D17. Ethereum L1 cannot be both, at a till. A
deployment that wants reorg-safe settlement without a 17-minute wait should
prefer the Polygon-class rails and treat `eth` as the demonstration rail it is.

`tools/reorg_probe` is what makes the residual exposure visible rather than
theoretical: it reports each booked sale's live depth and whether its containing
block is still canonical (D41). It does not remove the exposure and cannot.

See D11, D12, D15, D18, D41.

---

## D45 · A binding category is a claim about an adapter, not about a chain — 2026-08-30

D43 gave the library a `binding_category` so the app would stop inferring how a
payment binds. The first assignment was wrong, and it was wrong in a way worth
writing down, because the mistake is the natural one.

Four rails were declared `unconditional-per-sale`: `sol`, `usdc-sol`, `xmr`,
`xtm`. `usdc-sol` was corrected first, on the narrow ground that its own prose
says *"amount read from token balance deltas"* — the mechanism D33 proved is
"a race deciding which sale steals the money, not attribution". The remaining
question was put to Codex as a position: that the category is a fact about a
rail's **protocol mechanism**, so `xmr` and `xtm` may claim it unimplemented,
while `usdc-sol` may not.

**Codex found the position internally inconsistent, and it was right:**

> "For `xmr` and `xtm`, it classifies an ideal future implementation that
> allocates fresh identities correctly. For `usdc-sol`, it classifies one
> hypothetical bad implementation that reads balance deltas. That asymmetry —
> not implementation maturity — is the defect."

It is a real dilemma. Under the protocol reading `usdc-sol` *qualifies*, because
Solana Pay puts reference keys directly on `TokenProgram.Transfer`, so a future
adapter could decode the instruction exactly as the corrected SOL adapter
decodes System transfers. Under the mechanism reading `xmr` and `xtm` fail,
because freshness is adapter behaviour and nothing enforces it.

**Two claims were reproduced here before the position was abandoned:**

* `RequestRail.create_request` attaches a per-sale reference for `sol` and
  `usdc-sol` **and nothing else**. `uri.py` emits `tari://…?tariAddress={address}`
  and `monero:{address}?tx_amount=` — the recipient the operator configured,
  unchanged. So `xtm` and `xmr` have **no per-sale identity at all** in this
  codebase. A payment_id binds money to the id; it does not bind the id to a
  sale, and it is optional, arbitrary and not required to be unique.
* The overclaim is not inert. `catalog.declared_binding_category` lends a
  built-in declaration to any installed plugin that declares none, and three
  operator-facing surfaces print the result: `api.rails`, `tools/rails_probe`
  and `tools/snapshot` (which says `per-sale(claimed)`). A plugin using a static
  recipient would inherit "per-sale" and be shown as safely bound.

**The resolution.** A rail may claim an unconditional per-sale binding only when
two things are true **in code**: it gives each sale an identity of its own, and
its observer attributes the amount to the thing carrying that identity. D33 is
the proof that both halves are needed — Solana Pay's reference was a sound
protocol mechanism the whole time, and the rail still credited the wrong sale
until the adapter decoded the instruction. **A chain that could bind per sale has
not bound anything.**

So exactly one built-in rail claims it: `sol`. `xmr`, `xtm` and `usdc-sol`
declare `not-unconditional`, and each carries the reason in the table beside it.
The claim becomes true in the plugin that implements it, declared there, where
the code that makes it true lives — and `binding_category_for`'s pessimistic
default (D43) means an old plugin that says nothing is understated rather than
overstated.

**Pinned by rules rather than by a list**, both seen red:

* a rail declaring `unconditional-per-sale` must be in `catalog.REFERENCE_RAILS`
  — *"xmr claims a per-sale binding with no per-sale identity to bind to"*;
* a rail whose binding prose describes a balance delta may not claim one —
  *"usdc-sol credits a balance delta yet claims an unconditional binding"*.

`REFERENCE_RAILS` was named for this: the set was an inline tuple inside
`create_request`, so the invariant could only have been checked against a
hand-kept list, which is the kind of table this workspace has been bitten by.

See D33, D43, D5.

---

## D46 · Ootle is a working rail, and everything it needed was one version behind — 2026-08-31

the maintainer asked for testnet Ootle, rail first and then loyalty. Both work. What it
cost was not design: it was that **every pinned dependency in the Ootle stack
was a version behind a network that had moved**, and the first symptom looked
like a dead testnet.

### The blocker in the adapter was false

`ootle.py` refused SETTLEMENT with *"the indexer cannot bind a shared-account
balance change to a transaction"*, and `observe()` returned an unattributed
balance delta with a warning saying so. Against indexer **0.39.3** that is
simply not true:

```
GET /transactions/events/stream?substate_id=<vault>&topic=std.vault.deposit&after_id=<n>

event: std.vault.deposit
id: 247574
data: {"transaction_id":"157954d6…","event":{"substate_id":"vault_eec5267f…",
       "payload":{"amount":"1000000000","resource_address":"resource_0101…"}}}
```

Per-vault filtering, exact amounts, a transaction id, a monotonic cursor, and
replay from `after_id=0`. **It is the best observation primitive of any rail
here** — every other one rescans a range and can miss a payment between reads;
this one resumes from the last event it saw and cannot.

### It was not a testnet reset, and the epoch counter proves it

Every address in `ootle-testnet/ADDRESSES.md` returned 404 on **both**
indexers, including the account derived from the merchant's own key — while a
substate from a receipt minted minutes earlier returned 200 from the same
endpoint. But the epoch ran straight through: **9847** on 2026-07-27, **10092**
on 2026-08-05, **10765** on 2026-08-31, about 53 minutes an epoch. A reset
restarts the counter. This was an upgrade that took the state with it.

### Five version drifts, each found by running something

1. **The wire format.** `toolkit faucet` was refused by the indexer:
   `unexpected type null at position 251: expected u64`. That field is
   `max_epoch`, which `ootle-rs` 0.21 made a **required** argument to every
   builder; 0.16 sent null. Bumped, and 18 call sites plus `receipt.logs()`
   — removed from the struct in 0.39.3, `events` survives — were repaired.
2. **A duplicated crate.** `tari_ootle_common_types` left at 0.37 dragged a
   second `tari_engine_types` into the graph, and `want_substate` rejected its
   own argument: *"expected `SubstateId`, found a different `SubstateId`"*.
3. **Fees, twice.** `FAUCET_FEE` 1,000 against a required **2,182**; `CALL_FEE`
   5,000 against a required **6,705**. Both executed and were rejected on
   economics — the attempt spent for nothing, which the file's own comment
   warns about. Unused budget is refunded; an insufficient one is not.
4. **A response shape.** `chain._amount` refused a bare decimal string, and the
   indexer answers `{"Stealth": {"revealed_amount": "999997692"}}`. The library
   was rejecting its own live answer as "a shape this build does not
   recognise". **This reversed a deliberate test** that asserted a bare string
   *must* be refused rather than truncated; `_exact_integer` parses one exactly,
   so nothing is truncated by accepting it, and floats, dicts and bools still
   refuse.
5. **Inside the WASM.** `loyalty award` was rejected by the engine:
   `MintResourceArg … unexpected type array at position 4: expected u128`. An
   `Amount` that used to serialise as an array is a `u128` now. The template was
   rebuilt against `tari_template_lib` 0.31, which changed its address.

### Two defects in the new adapter, both found live and neither by a test

**The timestamp path was wrong, and the order was why.** The build order gave
the *values* of `created_at` and `finalized_at` without their nesting, so the
adapter read a flat `body["finalized_at"]` and its fixtures encoded the same
assumption. Tests green, every real payment unstamped. The live shape is
`transaction.summary.finalized_at`, checked across three real transactions. The
first live settle said `needs-review, credited=0, sighted=1234000` — it saw the
money and refused to credit it, which is the safe direction failing loudly.

**The SSE reader threw away good data.** Charging inside the containers failed
with *"the indexer did not answer: The read operation timed out"* while the
frames of a real payment sat in the buffer. The endpoint replays and then holds
the connection open; the host hands the reader four seconds. **A timeout with
frames in hand is the end of the replay, not a failure** — the events are
cursor-addressed, so a short read is a shorter answer and the next poll resumes
from the last id. Only an empty payload is silence. (`OSError`, not
`TimeoutError`: on 3.9 a read timeout arrives as `socket.timeout`, which is an
OSError and not a TimeoutError.)

### What is now true

A sale charged in ERPNext on `xtr`, paid by a real customer account on
esmeralda, settled by the library and booked:

```
CPS-2026-00438   $0.25 -> 5,000,000 microTari   confirmed / clean
paid    ccd237c28eba345b45757997ffe908e3be29944329bfd9e2affbd5f41fce714a
booked  ACC-SINV-2026-00100
points  2,500 awarded against sale ref CPS-2026-00438; balance reads 2,500
```

`toolkit devbench pay` was added because **no verb sent stranger → merchant**:
`open-account` goes the other way, `faucet` fills whoever signs, and `pocket` is
offline by design. It is signed by a key on this workstation, so it proves the
rail settles a real deposit — **not** that a stranger paid.

### What did NOT change, deliberately

**The binding is still the weakest one.** Deposits land in one shared merchant
account, so `xtr` is D5's binding — `not-unconditional`, exactly as `rails.py`
already declared. What improved is that a payment is now tied to a transaction
id, so the claimed-set can stop a double credit. Ootle *can* do better: a
payment component taking a sale reference would bind exactly, and that is a new
contract rather than an adapter change.

**The price is picked.** Tari is listed on no feed this build reads
(`live_tari_watch.py`, 2026-08-28, "NOTHING CHANGED"), so `xtr` charges from
`rates.DEMO_MICROCENTS`, which can never be reached in a real-money mode and
comes back `ok=False`, sourced `demo-fixed`. That reversed a test asserting the
demo table covers btc *and nothing else*. `rails.price_asset` says XTR should be
priced as XTM and **no caller consults it** — D26 made charging row-driven, and
the row says XTR. That gap is what must close before the entry can be deleted.

### A live address that answers and cannot work

The first republish, of the unmodified 0.29 crate, landed at
`template_078d574b…` — byte-identical to the one recorded on 2026-08-14,
because a template address is derived from its content — and the component on
it reads a perfectly good `promise()` and fails every award. Both are recorded
in `ADDRESSES.md` as unusable, because an address that resolves and answers is
exactly the kind that gets believed.

Gates: `make check` green — 100% line coverage, 2201/2212 mutants with the same
11 documented equivalents as before, 3.9/3.11/3.13/3.14, wheel. See D5, D19,
D26, D31, D33.

## D47 · A missing probe is not an unreachable rail, and the deployment was awarding points into a component that no longer exists — 2026-08-31

the maintainer asked to focus entirely on testnet Ootle and get it fit to show. Two
things were in the way, and neither was visible from any green suite.

### `xtr` was UNREACHABLE from all four workers, against an indexer answering in 0.51 s

`tools/reach_probe.py` shipped probes for `bitcoin`, `evm-native`, `evm-erc20`
and `solana`. It had none for `ootle`, and the unknown-family branch raises
`ProbeFailure("configuration error", …)`, which `run()` printed under the word
**UNREACHABLE**. So every worker reported

```
xtr  UNREACHABLE — configuration error: no reach probe exists for family 'ootle'
```

while `GET https://ootle-indexer-a.tari.com/network` answered
`{"network":"esmeralda","network_byte":38,"epoch":10775}` from inside the same
container in half a second.

**The cost was the whole of D40's third door.** `prove_end_to_end.py` runs
`reach_probe` in all four containers before spending, so
`prove_end_to_end.py --rail xtr` refused every run — the one tool that proves a
rail works could not prove the rail the maintainer wants to ship — and it closed with
*"Restore the configured endpoint's container reachability first"*, which is
advice for a fault that did not exist.

This is the **fifth** entry with the shape D25, D38, D39 and D40 share: a true
condition wearing a false sentence. The condition ("no probe exists") was
correct and the headline ("UNREACHABLE") was not, and the sentence is the half
an operator acts on.

Fixed three ways, because the fix for one family would not stop the sixth:

* `_ootle` makes the same `/network` read `OotleEsmeralda.readiness` itself
  makes, and takes the expected network from the **rail row's catalog key**
  rather than a hardcoded `"esmeralda"` — an endpoint repointed at another
  Ootle network is now a finding, which is the hazard `chain.py` warns about
  in its own words.
* `_classify` splits the headline: a configuration error reads **NOT PROBED**
  and says so — *"a gap in this tool, not a fault at the endpoint"* — and
  `prove_end_to_end.py` refuses with the matching advice. Both still refuse.
  A rail nothing can confirm is a rail nothing may charge on, so no exit code
  softened.
* **`reach_probe` was in no gate at all** — no Makefile target, no harness, no
  test — which is how the gap survived. `tools/h_reach_probe.py` (`make reach`,
  now inside `make check`) drives it from recorded answers with Frappe imports
  and every socket call forbidden. 25 checks. Its coverage check derives the
  required families from **adapter capabilities and `install.py`'s own
  ADAPTERS table, read with `ast`** — never from `_PROBES`, because a check
  that asked the probe table what the probe table should contain would have
  passed on the day `ootle` was missing.

Six `H_REACH_MUTATION` modes, every one seen red and none crashing:
`ootle_missing`, `unprobed_is_green`, `unprobed_says_unreachable`,
`ootle_any_network`, `ootle_any_epoch`, `ootle_hardcoded_network`. Live control
too: pointing `xtr` at `polygon-amoy-bor-rpc.publicnode.com` — a host that
answers — refuses with HTTP 404 while the real indexer passes, so reachability
drives the guard rather than mere configuration.

All four workers now exit 0 on all seven rails. `sol` is reachable in every
container as well, so **D39's DNS failure has cleared** — measured, not assumed.

### The deployment's loyalty component had been deleted from under it

D46 republished the loyalty template on 2026-08-31 because
`tari_template_lib 0.29` serialised an `Amount` as an array where the engine
now wants a `u128`, and a new WASM means a new template address. The new
addresses went into `ootle-testnet/ADDRESSES.md`. **`CryptoPoS Settings` was
never repointed.**

Asked of the chain rather than reasoned about:

| | `promise()` |
|---|---|
| `component_73f1d0bf…` — what the deployment was configured with | **404, does not exist** |
| `component_11d2dd28…` — what `ADDRESSES.md` documents | live, `committed_this_epoch: 2500` |

That 2,500 is D46's own award, sitting in the component the app was not
pointing at. `api.loyalty_status` returned `facts: null` and
*"the indexer answered 404 for substates/component_73f1d0bf…"* — so **loyalty
through ERPNext was dead**, degrading honestly and therefore quietly. Repointed
to the live pair; it now reads facts, six ceilings and a balance of 2,500.
Constructor parameters are unchanged and were checked on-chain, not assumed:
rate 100, ceilings 1,000,000 and 10,000,000.

**The old values are recorded here so the change is reversible:**
`component_73f1d0bff706282ebee60d51769a0c259a6bd8e7d58eb2fa3aa381fe14d70ae2`
and `resource_73c428292c39cdda71db621dc22c3899b4c7a3e11aecdb6058937e7d4f22fd48`.

### What is still open, and was found on the way

* **`harnesses/live_loyalty.py` still pins contract 1** — the dead one. That is
  what `h_docs.py` §13 was failing on when this session arrived (102/103,
  reproduced against a clean tree with the working-tree change stashed, so it
  is inherited and not caused here). The document was right and the pins were
  stale, which is the opposite of what the check's sentence suggests. Contract
  2's full set, read off the chain: template `985d07cc…`, component
  `11d2dd28…`, points `11ee7e60…`, entitlements `110dd385…`, enrolments
  `11642b84…`, vault claims `117d628f…`.
* **Three of the six addresses §13 flags are not contracts at all** — an XTR
  resource constant, a customer account, and a line the file itself says not to
  use. The check assumes every 64-hex address in a living document is a
  deployed contract some live probe should pin. Widening it is a judgement
  about what counts as pinnable, so it is left stated rather than taken.
* **The indexer is intermittent.** `points_balance` measured six times: 0.69 s,
  0.74 s, 0.92 s, 0.95 s, 6.82 s, and one that passed 15 s and timed out. The
  4.0 s default in `chain.READ_TIMEOUT_SECONDS` is not the problem and raising
  it would not fix this. Re-run before believing a single failure, exactly as
  §3 already says for Sepolia.
* **There is no bundled payer for `xtr`.** `prove_end_to_end.py --rail xtr` now
  clears the reach gate and stops at `customer_wallet.can_pay`, which lists six
  rails and not this one. The verb exists — `toolkit devbench pay <account>
  <microTari>`, *"the one direction no other verb sends"* — but the dev-bench
  key is sealed and needs `OOTLE_KEY_PASSPHRASE`, which is the maintainer's and which the
  toolkit's own help says is readable by any process running as the user. So an
  automated end-to-end Ootle proof is one decision away, not one build away.

Gates: `make reach` 25/25 and `make lint` clean. `make check` was **not** run —
it invokes `worth`, which rewrites `src/` in place while six containers import
that tree by absolute path, and the stack was serving throughout. See D5, D25,
D31, D38, D39, D40, D46.

## D48 · "exact-amount match" was false on all five shared-binding rails, in the library, the till and three published READMEs — 2026-08-31

the maintainer narrowed the goal: **Ootle is the only rail that will be offered publicly.**
That turned `xtr`'s binding from one weakness among seven into the whole
product's attribution story, so it was attacked directly — and the attack landed
somewhere nobody had looked.

### What every surface said, and what the code does

`rails.RAILS` described `eth`, `usdc-eth`, `pol`, `usdc-pol` and `xtr` as
*"static address + **exact-amount match** in the lock window"*. The published
`cryptopos-rail-evm` README said the rails *"match a payment by amount inside
the lock window"* and that nothing *"can tell two sales of the same amount
apart"*. `cryptopos-rail-ootle`'s README said *"a payment is matched by amount
within the lock window"*. `cryptopos-core`'s rail table said *"shared-account
exact-amount binding"*.

There is no amount match anywhere. `settle()` sums the **running total** of
unclaimed, timely transfers and settles the moment that total reaches the
invoice. Reproduced against the real adapters, offline:

| rail | invoice | one deposit arrives | result |
|---|---|---|---|
| `xtr` | 5,000,000 µT | 5,000,000 µT | settles |
| `xtr` | 100,000 µT | 5,000,000 µT | **settles, credited 5,000,000** |
| `xtr` | 1 µT | 5,000,000 µT | **settles, credited 5,000,000** |
| `eth` | 1 wei | 10¹⁵ wei | **settles, credited 10¹⁵** |
| `xtr` | 5,000,000 µT | 3,000,000 + 2,000,000 | settles (**summed**) |

So the true rule is *"the first open sale whose invoice the unclaimed timely
total covers"*. The whole two-sale sequence needs no matching amounts and no
attacker: sale A invoiced 100,000 µT and sale B invoiced 5,000,000 µT, B's
customer pays, **A polls first and settles on B's money**, and B — whose
customer actually paid — ends `needs-review` credited nothing. `watch.py`'s
`_claimed_transaction_ids` had already conceded the shape in its own words:
*"This is a defense, not a proof of exclusivity."* What was wrong was every
sentence describing it.

**This kills the obvious remedy before it was built.** Giving each sale a unique
amount is the standard trick for a shared address, and it does nothing here,
because the gate is a running total and not an equality. Any fix has to change
what `settle` compares, not what `charge` invoices.

### Why it survived

The claim was **frozen prose in a data table**, and prose in a table is not
executable. 650 core tests, 444 in the published tree, 100% line coverage and
full mutation coverage all pass with the sentence saying the opposite of the
code, because nothing asserts that a description matches a behaviour. It is the
`maturity: "works"` problem — legacy metadata nobody re-derived — wearing a more
dangerous sentence, and it is the fourth time this project has found a **true
condition attached to a false sentence** (D25, D38, D39, D40, and the `ootle`
reach probe earlier today).

The understatement mattered in the harmful direction. A reader of the EVM box
concluded they were safe unless two sales shared an amount, and priced their
risk accordingly.

### Corrected

`rails.py` in both trees now says *"running-total match … any covering deposit
settles it, whatever it was sent for"*. `xtr`'s `gate_text` — shown to a
customer at the till — said *"the recipient vault's revealed_amount rose by the
invoiced amount"*, which is both the wrong quantity and a leftover from the
pre-D46 balance-delta design; it now names unclaimed deposits totalling **at
least** the invoice. All three published READMEs carry the corrected sequence,
and `cryptopos-rail-ootle` gained a boxed warning at the top of the file
matching the EVM package's placement, because Ootle is now the only rail being
shipped and its disclosure was the weakest and most buried of the three.

650 core tests and 444 published-tree tests green after the change; `ruff` clean.
**The built wheels in `published/*/dist/` are now stale against these sources
and must be rebuilt before any republish.**

### Open, and deliberately not decided here

Whether to *fix* the binding rather than only describe it. The cheap lever
(unique amounts) is dead. What remains is either a per-sale account — Ootle's
`toolkit open-account` makes this cheaper than the EVM case D9 rejected, because
the merchant pays and fees are ~3,493 µT — or a payment component taking a sale
reference as an argument, which the ootle README already names as the real
answer and which is a smart contract rather than an adapter change. A
counter-argument on this is running. **the maintainer's call.** See D5, D9, D21, D25, D45,
D46, D47.

## D49 · The per-sale binding Ootle needs already works, and it was proved on a contract that is already deployed — 2026-08-31

D48 left one thing open: how to *fix* `xtr`'s binding rather than only describe
it. Two counter-arguments had killed both cheap remedies and converged on the
same answer — a payment component taking a sale reference — which the
`cryptopos-rail-ootle` README had already named as "a smart contract, not an
adapter change". The open risk was that nobody had checked whether such a
payment is **observable**. A component that binds perfectly and cannot be read
is worth nothing.

It is observable, and the proof needed no new code.

**Custom template events are indexed, namespaced by the template module.** The
deployed loyalty contract emits `emit_event("PointsIssued", metadata![...])`.
Filtering the event stream on topic `PointsIssued` returns **nothing**, which
looks exactly like "custom events are not indexed" and is why this was worth
measuring rather than assuming. The real topic is **`Loyalty.PointsIssued`** —
the module name is prepended. Unfiltered, the component's stream also carries
`std.component.created`.

Read from esmeralda today:

```
GET /transactions/events/stream?substate_id=<component>&topic=Loyalty.PointsIssued&after_id=0

event: Loyalty.PointsIssued
data: {"transaction_id":"708842dd…","event":{"substate_id":"component_11d2dd28…",
       "template_address":"985d07cc…",
       "payload":{"epoch":"10766","points":"2500","sale_ref":"CPS-2026-00438"}}}
```

**The payload carries the sale reference verbatim.** So a `Payments` component
emitting `PaymentReceived` with `sale_ref` and `amount` is readable as
`Payments.PaymentReceived` on the same per-substate, cursor-replayable stream
the adapter already uses for `std.vault.deposit` — the primitive D46 called the
best observation model of any rail here. That is an **unconditional per-sale
binding**: the money names the sale it is for, and no running total, amount
heuristic or payer assertion is involved.

What this closes, before any Rust is written:

* the remedy is not speculative — the mechanism is running in production on a
  contract deployed on 2026-08-31;
* observation needs no new transport, no extra request, and no change to how
  the adapter resumes from a cursor;
* the failure D48 reproduced (A settles on B's money) cannot occur, because
  attribution stops being an inference.

**Not yet built.** The component itself is unwritten — a Codex order for it hit
the account's usage limit 99 seconds in, and the toolchain is available here
(`cargo 1.97`, `wasm32-unknown-unknown`, and `ootle/loyalty` has built before).
Whoever writes it: name the module so the topic reads sensibly, because the
topic is the module name and it is part of the wire contract. See D5, D46, D48.

## D50 · The payment component exists, and D48's failure is gone in a test that reproduces it — 2026-08-31

D49 proved the mechanism was available. This is the component and the adapter
half, both written here after a Codex order for the same work hit the account's
usage limit 99 seconds in.

### The component

`Point of Sale/ootle/payments/` — a `#[template]` crate modelled on the deployed
`ootle/loyalty`, using its dependency versions rather than chosen ones for the
reason that file records: a promise about what an engine refuses is a claim
about a specific engine build. **159 KB of WASM against loyalty's 327 KB**, which
matters because a publish fee is quadratic in size and loyalty published fine.

`pay(payment: Bucket, sale_ref: String)` takes the reference as an ARGUMENT and
emits it. Nothing is inferred from amount, time, payer or polling order. Four
refusals, each with a test that sees it fire: an empty reference, one over 128
bytes, a bucket of the wrong resource, and an amount of zero. Withdrawal is
checked in the body against the operating key rather than named in an engine
rule, for the reason the loyalty template records — a component gate can only
name a FIXED rule, and a key that can never be replaced cannot survive a theft.

Two judgements worth arguing with later:

* **Duplicate references are recorded, not refused.** A customer who pays twice
  has made two payments; refusing the second strands real money in a contract
  with no refund path. The host's claimed-transaction set already stops one
  invoice being credited twice, and what changed is that attribution is no
  longer an inference.
* **The struct name is part of the wire contract.** The topic is
  `Payments.PaymentReceived`, so renaming `Payments` silently changes what every
  watcher filters on — and a watcher filtering a topic nothing emits sees no
  payments rather than an error. That is D49's trap pointed at ourselves.

10 engine tests, all passing.

### The adapter

`ootle.py` gained a component path, guarded so a rail with no
`payment_component` configured behaves exactly as before. Observation reads the
component's own event stream and keeps only payments whose `sale_ref` matches
the intent; a malformed event is a REFUSAL rather than a skip, because an event
this build cannot read might be a payment for this sale and dropping it silently
would under-credit someone who really paid.

**`create_request` takes no configuration, and D43 forbade adding a required
field to a published protocol.** So the component travels on
`RecipientBaseline.payment_component`, an OPTIONAL field with a default — the
adapter that captured the baseline is the one that knew the component. The
request's notice also changes: a plain transfer to the component address names
no sale and would never be credited, so saying "send to this address" there
would take real money and never credit it.

### The proof

D48's exact scenario, now a test rather than a script:

| | shared account (D48) | payment component |
|---|---|---|
| SALE-A, invoiced 100,000 µT, **paid nothing** | **settled, credited 5,000,000** | `pending`, credited 0 |
| SALE-B, invoiced 5,000,000 µT, **customer paid** | `needs-review`, credited 0 | **settled, credited 5,000,000** |

Green: 658 core tests (was 650), `make lint`, `make reach` 37/37, the Point of
Sale sweep at 1,762 checks and 0 failing, and 10 engine tests in the new crate.

### Not yet live, and why

The WASM is **not published**. `toolkit publish` signs a transaction and spends
merchant XTR, and the sandbox refused it — correctly; it is exactly the class of
action that should not happen without being asked for. Until it is published and
a `Crypto Rail` row names the resulting component, `xtr` still runs the shared
path and **must not be offered to strangers**. Nothing in this entry changes
that; it is what makes changing it possible. See D5, D43, D48, D49.

## D51 · Attacking my own payment component found a hand-rolled authorisation and a caveat no code closes — 2026-08-31

Codex was rate-limited, so the adversarial pass D50 asked for was done here
instead. Two findings, one fixed and one that is not a code problem.

### Fixed: `withdraw` hand-rolled authorisation the engine already provides

It read:

```rust
assert!(CallerContext::transaction_signer_public_key() == self.operating_key, ...)
```

`ootle/loyalty` uses `CallerContext::get_signer_proof_for_public_key(key).drop()`
for the same job, and its constructor comment calls capturing the signer *"the
defect rather than a detail of it"*. The two are not the same check:
`transaction_signer_public_key` is the key that **SEALED** the transaction, so
comparing against it demands the merchant be the sealer rather than merely a
signer — a merchant co-signing a transaction somebody else sealed would be
refused their own money. It is also hand-rolled authorisation where the engine
has a primitive. Now uses the primitive; 10/10 engine tests still pass.

Worth noting that the test suite did **not** catch this. `only_the_operating_
key_can_withdraw` passes under both versions, because a thief is refused either
way. The defect was in who else gets refused, and no test asked.

### Not fixed, because it is not a code problem: nobody has a wallet that can pay it

`pay` is a **component method call with a string argument**, not a transfer. A
customer's transaction must withdraw into a bucket and call the method. That is
the compose / sign / seal handoff this repository already built for loyalty
enrol and redeem — and `ootle/pocket`'s own docstring is blunt about what that
does not establish: *"A wallet written in this repository and run on this
workstation is the merchant's wallet no matter whose directory its key sits in.
Those rows ask whether a wallet somebody else controls signs, and the answer is
still no."*

So the binding is correct and a stranger still cannot use it, because the
product that would let them does not exist. That is board item R2, open and
external, arriving from a new direction. It does **not** make the component
pointless: it makes the library correct for anyone who adopts it, and it makes
the operator-only demo honest. It does mean a public anonymous till is further
away than "publish the WASM".

### Built so the rest is one command

`toolkit devbench pay-sale <component> <microTari> <sale-ref>` composes exactly
the transaction the component needs — `want_vault_for` on the payer's own vault
(the giftcard redeem path records learning that the expensive way), withdraw to
a workspace bucket, then `call_method(component, "pay", [bucket, sale_ref])`.
It compiles and refuses at the sealed-key gate before touching the network.

Green after all of it: 658 core tests, `make reach` 37/37, `make lint`, the
Point of Sale sweep at 1,762 checks and 0 failing, 10 engine tests.

**Three things this session cannot do**, listed so they are not rediscovered:
publishing the WASM (the sandbox refuses a signing, fund-spending command, and
correctly); `OOTLE_KEY_PASSPHRASE` for the sealed dev-bench key; and the
`delete_repo` scope for the force-pushed GitHub objects. See D43, D48, D49, D50.

## D52 · The per-sale binding is live on esmeralda, and D48's failure is gone against the real chain — 2026-08-31

the maintainer authorised publishing. The component is deployed, paid twice with real
XTR, and the real adapter attributes each payment to exactly the sale that
named it.

```
template   template_3547fb37e3fb6e5a7a284402c9acd0280bfd500c38c0d6bcf65f876956a4e65c
           tx ba1b5be63496ea0dc0c70d4754dbef8fc3da7e1055da0a90451a74d6e76f37e7
           159,477 bytes, fee 787,893 uT of a 7,504,477 budget
component  component_d7d8bb5a92c097e359e8d6e914e5b7cc9cff31072d1259daac114228d127e12f
           tx e88a70013b114d203f186d5151f6247f671d591d256134736fc371ff508fa78e
           XTR vault_d7693a565f407d5aa35bb122e117932085777dbf389736faf44265dc78b2547b
           fee 1,838 uT
```

Two real payments, then the adapter asked what they settled:

| sale | invoiced | outcome | credited | transaction |
|---|---|---|---|---|
| `DEMO-SALE-A` | 100,000 µT | settled | **100,000** | `f74985c4e419…` |
| `DEMO-SALE-B` | 5,000,000 µT | settled | **5,000,000** | `0afae1d88149…` |
| `DEMO-SALE-Z` | 1,000 µT, nobody paid | pending | 0 | — |

`DEMO-SALE-Z` is the control and it is the whole point. Under the shared-account
rule its 1,000 µT invoice is covered a hundred times over by either payment
sitting in that vault, and D48 measured that the first sale to poll takes them.
Here it is credited nothing, because neither payment names it. `DEMO-SALE-A`
likewise did not take `DEMO-SALE-B`'s 5,000,000 despite polling first — the
exact pair that failed in D48.

**This is real-chain evidence, not a fixture.** The rule this project has
learned four times over is that a green suite is not evidence a rail works; the
transaction ids above are.

### Two toolkit verbs, and what each does and does not prove

`toolkit payments deploy <template>` instantiates it, naming the operating key
rather than capturing the signer — `loyalty`'s constructor records why capturing
it was "the defect rather than a detail of it".

`toolkit payments pay <component> <microTari> <sale-ref>` is what made the
evidence above, and it is signed by **this machine's own merchant key**. Said
plainly rather than glossed: it proves the component binds a real deposit to a
named sale on the real network, and it proves nothing whatever about a stranger
paying. `toolkit devbench pay-sale` is the same transaction signed by somebody
who is not the merchant, and it needs `OOTLE_KEY_PASSPHRASE`, which this session
does not have.

Both compose the same shape: `want_vault_for` on the payer's own vault, withdraw
to a workspace bucket, then `call_method(component, "pay", [bucket, sale_ref])`.
The `want_vault_for` is not optional — `call_method` auto-adds vaults for the
component being CALLED, and the withdraw goes through `.then()` on the raw
builder, which adds no wants at all. The giftcard redeem path records learning
that the expensive way.

### Still true, and not fixed by any of this

The rail row in the deployment does not name the component yet, so the till
still runs the shared path. And D51's caveat stands: `pay` is a component method
call, so paying it needs a composed transaction the customer signs, and no
wallet a real stranger already holds does that. The binding is correct; the
product that lets a stranger use it is board item R2, open and external.

See D5, D48, D49, D50, D51.

## D53 · A sale charged, paid and booked through the per-sale binding — and the surface that would have prevented a stranded payment — 2026-08-31

`CPS-2026-00450`, 25¢, charged on `xtr` with the rail row naming the payment
component, paid through the component, settled and **booked as
`ACC-SINV-2026-00103`**. Credited 5,000,000 µT of 5,000,000, transaction
`ec653b7ad42ff2f4556edf75265fcf4c86a4f05be0780761d4b7e688010c06ac`.

That is the first sale in this project whose payment was bound to it by the
money itself rather than by inference.

### The wiring

`Crypto Rail` gained a `payment_component` field; `catalog.configuration_for`
passes it to the adapter; `charge` snapshots it into `identity_extras` beside
the endpoint — for the same reason the endpoint is snapshotted, so repointing
the row mid-flight cannot re-attribute money already in flight — and `watch`
reads it from the sale rather than re-reading the row.

### The mistake that found a real defect

The first payment attempt named `CPS-2026-00450`, the SALE name. The adapter
wants `payment_reference`, which `charge` sets to the **invoice ref**
(`4KQY-VVK3-K7X4`). So 5,000,000 µT went to the right component, for the right
amount, naming the wrong string — and was **correctly refused and stranded**.

The binding worked exactly as designed. What failed was the surface: `charge`
has been writing `payer_notice` into `identity_extras` all along and **no API
ever returned it**. On the shared path that was survivable, because the notice
only repeated an address the caller already had. On a payment-component rail it
is the whole instruction, and its absence is what stranded real money.

`status()` now returns `payer_notice`, and a fresh sale reads:

> Pay by calling this component's `pay` method with the sale reference
> `'VMVW-GA2M-WF49'`; a plain transfer to this address names no sale and will
> not be credited. Send exactly 5000000 microTari.

**The strongest evidence in this entry is the failure, not the success.** A
payment of the exact invoiced amount, into the exact component, was refused
because it named the wrong sale. Under D48's shared account it would have
settled something.

### A regression I introduced and caught in the same pass

Inserting the `_extras` helper put it between `@frappe.whitelist()` and
`status`, so the decorator landed on the private helper and `status` lost it —
exposing an internal as an endpoint and unpublishing a real one. `api_surface`
confirms `_extras` is not in the endpoint list and `status` is whitelisted
again. **Run `tools/api_surface.py` after touching `api.py`**; a decorator is
positional and a helper inserted above a function silently steals it.

Green: 658 core tests, `make reach` 37/37 and exit 0 in all four workers,
`make lint`, the Point of Sale sweep at 1,762 checks and 0 failing.
`api_surface` still FAILs on D37's three unscoped surfaces, which the
operator-only hosting decision answers rather than repairs.

Still open: the till's `binding` still reads `shared` for a
component-configured rail, because `binding_category` is declared per adapter
and not per configuration (D45). It understates what is now true. See D45, D48,
D49, D50, D51, D52.

## D54 · The binding label was understating the guarantee, and the rule lived in two places — 2026-08-31

D53 left the till reporting `binding: shared` for a rail bound by a payment
component. The money named the sale; the screen said it did not. That is the
same defect class as D48 pointed the other way — a true condition wearing a
false sentence — and the quieter direction, because understating a guarantee
makes nobody suspicious.

**The rule existed twice.** `charge.py` computed it for the sale record and
`api.rails()` computed it for the till's rail list, and the two versions
already disagreed: `charge` knew only about xpubs, `api.rails` knew about xpubs
and the declared category. Neither knew about a payment component. This project
has an entry (D35) about a rule in three places drifting in the one nobody
searched for, so the fix was one implementation, not two edits:
`catalog.binding_label(rail, mode)`, called from both.

**Configuration decides it, and the adapter's declaration is not overridden.**
D45 established that `binding_category` is a claim about an ADAPTER. Whether a
DEPLOYMENT binds per sale additionally depends on how it is configured: the same
Ootle adapter is `shared` pointed at a plain account and per-sale pointed at a
payment component, and no static declaration can know which. So the label is
computed from configuration in the app, and the library's declaration is read
rather than rewritten.

What the till reports now, and every value is the truth about that rail:

```
btc       per-sale     fresh address per sale from the merchant xpub (D7)
eth       shared       D5, and the EVM README boxes why
usdc-eth  shared
pol       shared
usdc-pol  shared
sol       per-sale     Solana Pay reference, per D31 as corrected by D33
xtr       per-sale     the payment component: the money names the sale
```

`CPS-2026-00452` charged and reads `binding: per-sale` with the payer notice
naming `V33Q-ZCX4-VV3G`.

### A regression on the way, caught by looking

The first version returned "" for every rail, because `api.rails()` computed
`mode` only when `with_readiness` was asked for and I passed `None` into a
function that needs it. A whole column of the till went blank because a
variable was computed conditionally for a different caller's benefit. `mode` is
now read unconditionally.

Green: 658 core tests, `make lint`, `make reach` 37/37 and exit 0 in all four
workers, `rails_agree.sh` OK, the Point of Sale sweep at 1,762 checks and 0
failing. `api_surface` still FAILs on D37's three unscoped surfaces, which the
operator-only hosting decision answers rather than repairs.

See D5, D7, D31, D33, D35, D45, D48, D53.

## D55 · A stranger paid a sale with their own key, and the reason nobody could before was one crate version — 2026-08-31

`CPS-2026-00453`, 25¢ on `xtr`, `binding: per-sale`, paid by a wallet holding a
key this till has never seen, settled and **booked as `ACC-SINV-2026-00104`**.
Transaction `1d28f26ee9611404118670ffc31b29449d5f0cb4916d9cd2db35f02f97056a68`.

D51 concluded that no wallet a stranger holds could pay a payment component and
called it board item R2, open and external. That was wrong in the useful
direction: the capability was already in this repository and had been broken
since the day before.

### The path, and who held what

1. `pocket address` created a fresh wallet with its own key in its own
   directory. The till never sees it.
2. `toolkit open-account <address> 6000000` — the merchant paid to bring that
   account into existence, because an Ootle account does not exist until
   somebody creates it.
3. `api.charge(25, "xtr")` → `CPS-2026-00453`, invoice ref `RWJT-XAE4-V3H7`.
4. `toolkit payments pay <component> 5000000 RWJT-XAE4-V3H7 --member <pubkey>
   --account <component> --compose stranger.json` — composed against the
   CUSTOMER's account and written out unsigned, holding no customer key.
5. `pocket read stranger.json` — and this is the part that makes it consent
   rather than compliance. It decodes the instructions **from the bytes**:
   withdraw 5,000,000 XTR from *your* account, then call `pay` on the component
   with argument `"RWJT-XAE4-V3H7"`. It also showed that the FEE is paid by the
   merchant's account, which nothing had established before.
6. `pocket sign` → `toolkit submit-request`. Settled, booked.

### What was actually broken, and for how long

`submit-request` first refused with *"produced by \<the right key\>, but not
over these bytes sealed by \<the merchant\>"*. The cause was not the signature:

```
toolkit  ootle-rs 0.21   tari_ootle_transaction 0.39
pocket   ootle-rs 0.16   tari_ootle_transaction 0.37
```

The toolkit was moved to 0.21 on **2026-08-30** with the note *"esmeralda runs
indexer 0.39.3 and REFUSED a 0.16-built transaction -- the wire format moved;
these follow it."* `pocket` was not moved with it. The two then serialised
`UnsignedTransaction` differently, so the customer signed one set of bytes and
the till verified another. **A signer one version behind the sealer is a signer
whose consent cannot be used.**

This is D46's finding repeating inside our own tree: everything in the Ootle
stack was one version behind a network that had moved, and the fix moved five
things and missed the sixth. Bumping `pocket` needed two source changes as well:
`Instruction::EmitLog` is gone from the 0.39 enum, and `max_epoch` stopped being
optional.

**Nothing said so.** The toolkit's three signing-request tests have been failing
since that bump — reproduced on a clean tree with today's work stashed, so
inherited — because their fixtures were composed under the old format and no
longer decode. And `harnesses/run_all.py` does not run `cargo test`, so a green
sweep sat on top of a dead signing path. The compose/sign/submit handoff had no
live exercise anywhere.

### What is now true, and what is still not

A stranger CAN pay a bound sale, with their own key, seeing what they sign. What
remains is **distribution, not capability**: `pocket` is a binary from this
repository that a customer must obtain and run. That is a smaller and more
ordinary problem than "the product does not exist", and it is the honest
statement of where R2 now sits.

Still open: the toolkit's three fixture tests, which need regenerating under the
current wire format, and which no gate runs. See D46, D48, D50, D51, D52, D53.

## D56 · The sweep now runs cargo, and it failed on its first run — 2026-08-31

D55 found the customer-consent path had been dead for a day and that nothing
reported it, because `harnesses/run_all.py` counted 1,762 Python checks and
printed `clean` while never once running `cargo test`. Both halves are closed.

### The fixtures, regenerated and now regenerable

The toolkit's three signing-request tests and four of `pocket`'s ten were
failing on fixtures composed under `ootle-rs` 0.16, which stopped decoding when
each crate followed esmeralda to 0.21/0.39. They were regenerated from live
artefacts made today — a real composed request, a real signature from a key the
till has never held, and the Python wire payload re-encoded through
`qr_wire.from_file`.

**Each fixture file now carries the command that remakes it.** The old set had
no such note, which is why a wire-format bump silently killed it rather than
prompting anyone to regenerate. A fixture nobody can remake is a fixture that
dies at the next bump.

Two assertions moved with the fixtures, and the move is an improvement rather
than a concession: they asked whether a signer is shown *their vault* on a
loyalty enrol, and now ask whether a signer is shown *their account, the amount,
and the sale reference* on a payment. The added one matters most — a renderer
that showed the money and hid which sale it settles would let a customer
authorise the right amount against the wrong invoice.

Toolkit 4/4, pocket 10/10, payments 10/10, loyalty green.

### The gate

`run_all.py` now runs `cargo test --release` for every crate under `ootle/` that
has a `Cargo.toml`, and **a missing toolchain is reported rather than skipped**:
it prints `UNVERIFIED - cargo is not on PATH, so these did NOT run` and fails.
"The checks did not run" and "the checks passed" must never look the same --
the rule `audit-secrets.sh` learned when it printed `ok` for a refusal it had
not observed.

**It caught something on its first run.** `pocket` reported 4 failures the
moment the gate existed, in a crate that had looked fine all day because
nothing had ever asked it.

The cost is honest: the sweep went from **6.2 s to about 190 s**, almost all of
it the loyalty crate's engine tests. That is the price of the Python half no
longer being able to report `clean` over a dead Rust half, and it is worth
paying. If it becomes a problem the answer is a faster loyalty suite, not a
quieter gate.

See D46, D51, D55.

## D57 · Two reviews, two refusals, one real settlement bug, and a scan of mine that was wrong — 2026-08-31

the maintainer authorised building a public surface **if the review cleared it**. It did
not clear it. It is not built.

### The public surface: refused, and the refusal is right

Proposed: one read-only, unauthenticated endpoint returning a single sale's
payment request, keyed by its invoice reference. I checked the entropy myself
first — `secrets.choice` over 27 characters, 12 long, **57.1 bits**, 1.5×10¹⁷
possibilities, not enumerable — and that turned out to be answering the wrong
question.

> "The endpoint may be database-read-only, but its output is not passive: it
> publishes the exact bearer data needed to select a live sale for an
> irreversible on-chain write."

That write drives `awaiting → confirmed` and creates a real Sales Invoice. The
reference is not a lookup key, it is a **capability**: whoever holds it can
cause ERPNext to book that sale. Serving it to anyone who asks hands out that
capability, and unguessability is irrelevant when you are publishing the guess.

What survived, and would be the shape to build:

* a **separate service** holding no database credentials, serving a published
  artifact written by a writer that runs after `charge()` and after every state
  transition, replaced atomically;
* keyed by a **new 128-bit token**, never `invoice_ref` — reusing it lets the
  public event stream enumerate ERP lookups, because the chain's copy of the
  reference is public;
* Cloudflare serving and rate-limiting it, with ETags so settlement-watching
  never touches Frappe.

And a limit no ERP change reaches: **one publicly identified component emits
every payment**, so shop-wide takings stay correlatable from the public stream.
Concealing that needs per-sale components or a different event commitment — a
chain-level change, not another endpoint.

### The blob scan I got wrong, twice

I reported "zero occurrences in any blob" for all four published repositories,
in two separate messages. It was false. `git grep … $(git rev-list --all)`
searches only **reachable** objects, and the pre-squash blobs were unreachable
but still in the object database — the review named three by SHA and all three
read back with `git cat-file -p`:

```
cryptopos-rail-bitcoin  ee5cd699454a2ec0e6e5ffa30f9993e4021665e5
cryptopos-rail-evm      81e6db5bba4970d467f9e9c3ecc83be185d7deb3
cryptopos-rail-ootle    1323ffea7b973f73abb81070133c650885aa4cd4
```

The method that actually answers the question is `git cat-file
--batch-all-objects`. After `reflog expire --expire=now --all` and `gc
--prune=now`, all four repositories read 0 by that method.

**This makes the standing GitHub residual concrete rather than theoretical.**
Those objects were force-pushed; GitHub keeps unreachable objects fetchable by
SHA until its own collection. Deleting and recreating the repositories is the
certain fix, and it needs a `delete_repo` scope this token does not carry.

### The settlement bug, reproduced and fixed

`settle()` tested `block_time <= expires_at` and nothing else, so a deposit
dated a **day before the sale existed** settled it. Reproduced. The cursor is
what should make that unreachable, but `chain._get_sse` deliberately treats a
timeout with frames in hand as the end of a replay — a real fix for a real
problem — and therefore cannot distinguish a finished replay from a truncated
one. A short baseline over a long history puts pre-existing money after the
cursor.

Now bounded at both ends, with `_CLOCK_SKEW_SECONDS = 3600` rather than zero,
because a tight wall-clock lower bound is **D19 wearing the opposite sign**: that
was a nine-hour timezone error which made every real payment look late and was
invisible to a fully green suite. Half an hour of skew still settles; a day-old
deposit goes to `needs-review` rather than being silently dropped, so an
operator sees it. Three tests pin all three cases. Ported to the published rail
package: core 661 tests, published core and rail suites green.

### One claim that did not reproduce

The review said `/transactions/{id}` holds only transactions submitted through
that same indexer, so a stranger paying via another wallet would 404 and be
routed to review. **Not reproduced.** `ootle-indexer-b.tari.com` exists and
returns HTTP 200 on both `/transactions/{id}` and `/transactions/{id}/result`
for a transaction submitted through indexer A. The documented caution stands;
the failure mode does not occur on esmeralda today.

### The publication verdict

The second review says do not publish, and lists blockers beyond the two fixed
here — core's 2.0 documentation describing the wrong plugin boundary, Bitcoin's
binding claim, and missing licence files in the rail distributions. Those are
unexamined here and are not claimed to be closed. See D19, D48, D52, D55.

## D58 · Two ways the Ootle rail could credit money that was not a payment — reproduced and closed, 2026-08-31

Both were found by an adversarial review that said *do not publish*, and both
were reproduced here before being believed. Neither was visible to a suite that
was green at 661 tests, 100% line coverage and full mutation coverage.

### The rail said "committed" and never asked

`settle()` has always returned the reason *"committed Ootle deposits are
final"*. Finality on Ootle is a property of a **committed** transaction — and
nothing in this adapter had ever read `summary.outcome`. It read
`summary.finalized_at` beside it, from the same object, and stopped there.

Reproduced on **both** paths, including the new per-sale binding: a transaction
summary reading `{"outcome": "Abort", "finalized_at": ...}` settled a 5,000,000
microTari sale and would have booked it, under a reason asserting the exact
thing that was false. That is this register's most-repeated shape — a true
condition wearing a false sentence (D25, D38, D39, D40) — and here the sentence
was the guarantee itself.

An uncommitted transaction is now **reported and not credited**: it becomes an
unconfirmed `TransferObservation` with no block time, and the warning names the
outcome. It is not dropped, because an indexer wrong about an abort would then
rob a customer who really paid. A missing or unreadable outcome is treated the
same way — fail-safe toward not booking goods.

The rule lives in **one** function, `_observed_transfer`, called by the shared
path and the component path. D35 is why: the copy nobody searches for is the
copy that keeps the defect.

### A baseline was the end of one read, not the end of the history

`capture_baseline` called the event stream once. `chain._get_sse` returns what
it has when its budget runs out — correct for observation, where the cursor
makes a short read a *shorter* answer rather than a wrong one, and wrong for a
baseline, where the cursor is the claim *nothing before this point is mine*. A
shared account with more history than four seconds of replay handed back a
cursor in the middle of its own past, and every deposit after it looked like a
payment for a sale that did not exist yet. No attacker required; only time.

The baseline is now **drained** — read, advance, read again — and a history that
will not end within twelve pages is a refusal rather than a guess.

**And the first version of the drain was wrong, because it trusted the
docstring.** `_get_sse` says the endpoint sends a `:` comment while idle and
calls that the replay boundary. Measured against `ootle-indexer-a.tari.com`:

```
after_id=0       4.56s  1983 bytes  5 events   tail = a complete data frame
after_id=250652  4.34s  NO PAYLOAD              (the read simply timed out)
```

That comment never arrives. **Every** read on this endpoint costs its full
timeout, and the end of a history looks like silence. The drain refused every
baseline on the deployment it was written for until it was taught the
difference — silence *after* a page has answered is the end; silence on the
first read is still a dead endpoint; and an unparseable stream stays a refusal
at every attempt, because silence and nonsense are different answers.

The measured cost is honest and is written down: `capture_baseline` on the live
component takes **9.7 s** (two reads), and a charge on `xtr` takes **14.5 s**.
`chain._get_sse`'s docstring now records that its stated boundary is not the one
that occurs here.

Settlement is bounded at both ends (D57) and its reason no longer names only one
of three causes — "after expiry" was already false for a deposit predating its
sale, which D57 introduced and this corrects.

### The gate's excuses had gone stale, and that failed silently

`make worth` reported eight survivors. Six were killed with new boundary tests —
the twelve-page bound on both sides, the hour of clock skew to the second, the
128-byte reference at its accepted edge, a one-microTari amount, the
confirmation count on a committed transfer with an unreadable clock. The
seventh, `plugin.py:394`, was **already triaged** with a correct reason under the
key `plugin.py:379`: fifteen lines had been inserted above it and the entry
stopped matching anything.

`EQUIVALENT` is keyed by line number, and line numbers move. A triage entry that
stops matching fails **toward more work**, so it never looks like a defect in the
list. `worth.py` now checks its own list against the run: every entry must name a
mutation this run produced *and* that survived. It found **six** stale entries on
its first execution, and all six turned out to be obsolete rather than misfiled —
`rates.py` 108/108, `evm.py` 264/264 and `catalog.py` 32/32 kill every mutant
they produce, and `addresses.py:127` is killed at its new line. The suite had
grown past all six excuses and nobody knew.

Green after: 678 core tests, `make check` clean across 3.9/3.11/3.13/3.14 and
against the built wheel, `make prove` 100% with every symbol registered,
`make worth` 2262/2273 with 11 accepted, the app harness 85/85, `rails_agree.sh`
OK. `xtr` charged live: `CPS-2026-00464`, `binding: per-sale`, reference
`HMQW-4T3X-642N`.

See D19, D25, D35, D46, D48, D49, D50, D53, D57.

## D59 · Asking what a rail's binding is was allocating a Bitcoin address — 2026-08-31

D54 merged two copies of the binding rule into `catalog.binding_label`. The
merged function opens with:

```python
if not (recipient_for(rail, mode) or ""):
```

`recipient_for` does not report where money is received. On a rail with an xpub
it **derives the next address, advances `next_address_index`, and holds a
`FOR UPDATE` row lock** while doing it. So a function whose name says it labels
was spending the operator's addresses to answer a question.

Reproduced: one `api.rails()` — what a till does every time it draws its rail
list — moved `btc`'s `next_address_index` from 2 to 3, with no sale in
existence. `charge()` was worse: it called `recipient_for` for the address and
then `binding_label` called it again, so **every real Bitcoin sale consumed two
indices and recorded the first**.

Nothing was stolen and no money is unrecoverable — an xpub re-derives any index.
What it costs is BIP-44's twenty-address gap limit, eaten at twice the rate, and
past that gap a wallet restored from the account key stops scanning and does not
find the money. The rails list is also reachable from an endpoint with no role
guard (D37), so the counter could be advanced by anything that could call it.

`catalog.receiving_material(rail, mode)` is the pure predicate the label
actually needed — it reads the row and returns `xpub`, `component`, `address` or
`""`. `binding_label` is built on it and allocates nothing. Verified: five
consecutive `api.rails()` calls now consume **zero** addresses, and every rail's
reported binding is unchanged.

It is the same shape as the defect it was introduced to fix: a function whose
name says it reports, doing something underneath.

**And the rule had a fourth home.** `tools/snapshot.py` computed its own,
knew nothing about payment components, and printed `xtr SHARED` on the same
afternoon the till correctly printed `per-sale`. It calls `binding_label` now,
and keeps its own distinction between a binding that is a FACT (a derived
address, a named component) and one that is a CLAIM an adapter makes about
itself (D33) — which is real information the shared function does not carry.

**A third copy of the prose was in the database.** `Crypto Rail` rows hold a
frozen snapshot of the library's `binding` and `gate_text`, refreshed only by
`seed_rails()` on migrate. D48 corrected "exact-amount match" in the library, the
till and three READMEs; the operator's own row still said it hours later. The
library's sentence was itself stale in the other direction — it called the
payment component a thing that "would bind exactly and is a new contract", which
stopped being true the day it was deployed. Both corrected, and the rows
re-seeded: 11 fields refreshed.

See D33, D35, D37, D45, D48, D54.

## D60 · What the published packages had to catch up on before the tunnel could go in front of them — 2026-08-31

Two cold reviews said *do not publish* and listed blockers. Every one below was
reproduced here before it was fixed; the reproductions are what make them
findings rather than opinions.

### The published rail was a version behind the rail that works

`published/cryptopos-rail-ootle` had **none** of the per-sale payment component
(D49–D54). Publishing it would have shipped, as the only mode, the shared-account
binding that D48 measured settling the wrong sale — with the safe mode sitting
in the working tree unshipped. The package is now regenerated mechanically from
the proven core source, and the only differences are three intended ones: the
distribution's own `__version__`, the import rewrites, and `_coerce_integer`
**inlined rather than imported**.

That last one is the review's finding and it is real: the package declared
`cryptopos-core>=2,<3` while importing a leading-underscore name from it. A
resolver honouring that range may pick a later 2.x that has moved the private
helper, producing an install that succeeds and a rail that fails on import.
Twenty lines copied verbatim is cheaper than a dependency on somebody else's
private detail.

Rail suite: 131 → **159 tests**, green. The abort and drain reproductions were
re-run against the published package, not only against core.

### Three distributions shipped `License: MIT` and no licence

Verified in the built wheels and sdists: no `LICENSE` file in bitcoin, evm or
ootle. Recipients of `chain.py` — this project's own code, extracted from core —
were given no copyright notice, no permission grant and no warranty disclaimer.
Fixed and verified in the rebuilt artifacts:
`<dist>-info/licenses/LICENSE` present in every wheel, `LICENSE` in every sdist.

### Documentation that described a package that no longer exists

Executed rather than read, all against the built 2.0 wheel:

* `registry.get("bitcoin:testnet4/native:btc")` after the README's own
  quickstart → `RailNotInstalled`. `register_builtins()` registers **six
  request-only** rails, none of them Bitcoin, EVM or Ootle.
* `import cryptopos_core.chain` → `ModuleNotFoundError`. The README used it in
  an example and the package docstring advertised it, along with `bitcoin`,
  `evm` and `ootle`.
* The README claimed 579 tests; the suite runs 444.
* The "Built-in scope" table listed rail-package capabilities as core built-ins,
  and marked `Polygon Amoy / POL` as observe-no / settle-no when
  `cryptopos-rail-evm` drives it.

All corrected against what the packages actually do, the table rebuilt from an
enumeration of the `cryptopos.rails` entry points in an environment holding all
four distributions.

`SECURITY.md` said Ootle attribution "relies on the static account plus exact
amount inside the lock window". That is D48's false sentence surviving in the
**security model document** — the claim is stronger than the code makes, and
unique per-sale amounts are not a remedy for a running total. Corrected there
and in the EVM README, which had the same sentence and had shipped it inside a
wheel's `METADATA`.

Bitcoin's README called its binding "per-sale — a fresh address derived from the
merchant's watch-only account key". **The adapter derives nothing**; it refuses
an address with transaction history and the host supplies the address. The
distinction matters because the check cannot see the case that hurts: two
concurrent sales handed the same never-used address both pass `capture_baseline`,
and the first to poll takes the other's money. Now stated as the sequence.

And a stale test: `test_installed_version_and_rail_entry_points_match_the_source`
asserted core advertises four entry points naming `cryptopos_core.bitcoin` and
`cryptopos_core.evm` — modules the 2.0 split had already removed. It had been
failing since. It now asserts core advertises **none**, which is the load-bearing
claim of the split.

Published suites after: core 444, bitcoin 32, evm 43, ootle 159 — all green. The
built ootle wheel was installed clean on **Python 3.9**, resolved through its
entry point, and drives the payment component.

### The origin the tunnel would have fronted was answering 502

Found by accident, testing a `Host` header. The frontend had been returning
**502 Bad Gateway to every request for about two hours** and nothing in this
repository noticed. `rails_agree.sh` was green, `reach_probe` was green in all
four containers, the app harness passed 85/85 — every one of them asks about
worker processes, and none asks whether the HTTP surface answers. The cause was
ordinary: the backend restarted, took a new address on the Docker network, and
the frontend's nginx held the old one. `nginx -s reload` fixed it.

`erpnext-hr/origin_probe.sh` now asks the question a published instance lives on:
the origin answers 200 under the local name **and** the public one, Frappe itself
answers `/api/method/ping` through the public host header, port 8080 **refuses
every non-loopback address on this host**, and `cloudflared` routes the hostname
to that origin. Reachability is asked from outside loopback because a bind to
`0.0.0.0` is invisible from `127.0.0.1` — the check that matters cannot be run
from the side that always works.

**Every check has been seen red**: the backend stopped (3 fail, exit 1), a
throwaway listener on `0.0.0.0` (3 fail on three interfaces), and a hostname the
ingress does not route (1 fail). A check that cannot be seen failing is a check
nobody knows the direction of. It reports `UNVERIF` and exits 1 rather than
passing when it cannot run — `audit-secrets.sh` learned that the expensive way.

### Where publication stops, and why it stops there

Ready and verified: the ingress rule for `<the published hostname>` →
`http://127.0.0.1:8080` (`cloudflared tunnel ingress validate` OK, rule matching
checked for three hostnames), the site's `host_name`, and the origin probe green.

**No DNS record was created and the tunnel was not started.** CONTINUE.md §1
Decision 2 makes the instance operator-only behind Cloudflare Access, and
`~/.cloudflared/cert.pem` carries tunnel scope only — it can create a DNS route
and cannot create an Access application. A DNS record plus a running tunnel
without Access is the ERPNext login on the public internet, in front of a
surface where any authenticated `Sales User` can fabricate a settled sale that
the five-minute `sweep_unbooked` turns into a real Sales Invoice. the maintainer chose
Access first, which is the order Decision 2 already implied.

See D37, D48, D49, D57, D58.

## D61 · A cold review of D58's own fixes found a wrong-sale booking in them — 2026-08-31

D58 closed two ways the Ootle rail could credit money that was not a payment.
Attacking that work — the habit `CLAUDE.md` requires before an architecture
call, applied here to a change already made — found **six defects in the fixes
themselves**, one of them worse than what D58 had closed. Every claim was
reproduced before it was accepted, and one was reproduced only after correcting
my own test harness, which had been asking the wrong question.

### The one that mattered: a 503 mid-drain settles the wrong sale

`_drain` stopped when a page returned nothing, and "nothing" was
`payload is None` — which `chain._get_sse` returned for an idle stream **and**
for an HTTP 503, a DNS failure, a TLS failure and a connection reset. So:

1. Baseline page one returns events through id 100.
2. The read for `after_id=100` gets a 503.
3. The drain reads that as the end of the account's history and returns 100.
4. A new sale opens for 5,000,000 µT.
5. The indexer recovers and replays a **30-minute-old** deposit as event 101.
6. It is committed, unclaimed, inside the window — and it settles the new sale.

Reproduced exactly. **This is the failure D58's drain was built to prevent,
reintroduced by the drain's own termination test.** A guard whose stopping
condition cannot tell success from failure is not a guard.

The fix is at the layer that owns the distinction. `chain._get_sse` now tracks
whether the connection was **established**: a socket that opened and then went
quiet returns empty bytes (the endpoint answered, and had nothing), while one
that never opened returns `None`. `_replay` refuses anything that returned
`None`, at every attempt, and the `silence_ok` flag is gone — there is nothing
left for it to be wrong about.

### Five more, all reproduced

**A fresh payment component could not take its first sale.** Its event stream
is legitimately empty, the read timed out with no bytes, and that was a
refusal. The same fix closes it: an empty answer is an answer. Verified — a
component with no history now yields a baseline of 0 and charges.

**The twelve-page bound gave the rail a finite lifetime.** Every baseline
replays from zero, so a component would hit the bound at roughly its sixtieth
event and then refuse **every** later sale. That is not slowness; it is an
expiry date, on the one path this project publishes.

The fix is an asymmetry that should have been there from the start. On the
component path the money **names the sale** — a 57-bit reference minted at
charge time cannot appear in an older event — so the cursor is an optimisation
and a short one cannot misattribute anything. That path now reads **one page**.
Draining is kept only for the shared account, where the cursor *is* the
attribution, and its refusal message names the remedy that exists. Measured:
a component with 200 events of history charges, and its next sale settles.
The live charge went from **14.5 s to 10.5 s**.

**One transient 503 on the transaction read cost a customer their sale.**
`_observed_transfer` discarded the reason, so "the indexer says Abort" and
"the indexer did not answer" produced the same unconfirmed transfer under the
same sentence — *"an uncommitted transaction moved no money"* — which the code
had no evidence for. `needs-review` is terminal (D10), so the indexer
recovering a second later changed nothing.

`_Unresolved` now carries *the provider did not answer* as a thing distinct
from *the provider said no*, and settlement returns **PENDING** for it: nothing
was decided, so keep polling. **And that alone was only half a fix** — the
cursor advances past an event once seen and `extend` refuses a page repeating a
transaction id, so the doubt could never be revisited and the sale merely
stopped being wrongly refused and started quietly expiring, which costs the
customer exactly as much. `_resolve_outstanding` re-asks about those
transactions on the next poll, before reading anything new. Proven both ways: a
recovered read settles the sale, a still-failing one stays pending.

**The transaction body's own id was never checked.** An event naming
transaction A paired with a cached body for transaction B took B's `Commit` and
timestamp and credited A's amount — the outcome check certifying money it had
never looked at. Now a mismatch is unresolved, not settled.

**`ObservationBatch` gained `unresolved_transaction_ids`**, optional with a
default, for D43's reason. That makes the rail depend on a core feature, so
published core is **2.1.0** and the rail's floor is `>=2.1,<3`. Verified by
resolution rather than by reading: offered only core 2.0.0, the resolver now
refuses instead of producing an install that succeeds and fails on the first
sale.

### What the gates did and did not do

`make worth` caught four survivors afterwards, and one of them was real: the
`connected` flag had no test, because every stub returned bytes immediately and
never exercised a timeout on an open socket. Another was a redundant
`credited < amount` in a branch the settled path had already returned from —
deleted rather than excused, because a dead condition is how code rots.

**And the EQUIVALENT staleness check D58 added earned itself twice in one
session**: `plugin.py:394` moved to `413` when the new field was added, and the
check said so instead of reporting a survivor that had been explained months
before.

Green: 686 core tests, `make check` across 3.9/3.11/3.13/3.14 and against the
built wheel, `make prove` 100% with every symbol registered, `make worth`
clean, published suites core 444 / bitcoin 32 / evm 43 / ootle 167,
`rails_agree.sh` OK, `origin_probe.sh` green. Live: `CPS-2026-00475` charged on
`xtr`, `binding: per-sale`.

**The lesson is the one this register keeps recording, one level up.** D58's
fixes were reasoned about carefully, tested, mutation-tested and green — and
two of them were wrong in the direction they were meant to protect. What found
it was not a gate. It was asking somebody who had not written them.

See D10, D19, D43, D48, D57, D58.

## D62 · The instance is published, and the check that matters is not the one that is easy — 2026-09-01

`https://<the published hostname>` is live: ERPNext through the existing Cloudflare
tunnel, behind an Access application whose single policy admits one email and
denies everything else. Decision 2 of `CONTINUE.md` §1 is satisfied rather than
deferred.

### Two credential facts that cost a round trip each

**A 200 from a read endpoint proves Read.** The `cfat_` account token listed
Access applications cleanly, and that was reported here as though it settled
whether the token could create one. It could not: `POST .../access/apps` →
`1010 auth.forbidden`. The working token is a `cfut_` **user** token with
Access: Apps and Policies **Edit**, expiring 2026-09-11.

**Neither token can touch DNS.** The record was created by `cloudflared` off
`cert.pem`, and the `discord-activity` record was deleted through the API token
embedded inside that same file — which is worth knowing, because it means
`cert.pem` carries more authority over the zone than either API token issued
for this work.

### The verification, and the two checks that were worthless

The easy check is worthless: `https://<anything>.cloudflareaccess.com` returns
**200** for a team name invented on the spot, so "the team domain answers" says
nothing. The discriminator is `/cdn-cgi/access/get-identity` — **400** on a real
organization (it exists, you have no identity) against **404** and a 34 KB error
page on one that does not.

The second worthless check was mine. Five surfaces were tested unauthenticated
from outside — the desk, `/api/method/ping`, `cryptopos.api.rails`, `/login`
and a static asset — and one came back **NOT GATED**. It was the detector: the
Access sign-in page echoes `redirect_url`, the asset path is
`/assets/frappe/images/...`, and the string `frappe` in the response was my own
request reflected back. Confirmed by looking: the body is not an SVG, carries
`cdn-cgi/access`, and both matches sit inside the reflected URL. **A security
check must not be wrong in either direction**, and a false "open" is as bad as
a false "closed" because it sends the next session chasing a hole that is not
there.

All five land on `<team>.cloudflareaccess.com`, title *Sign in ・ Cloudflare
Access*, no Frappe markers.

### What is published, exactly

One hostname. The `discord-activity` ingress rule was removed at the maintainer's request
and its DNS record deleted — confirmed gone from `1.1.1.1`, `8.8.8.8` and the
zone's own authoritative nameserver, which is worth checking separately because
the local resolver served the deleted name from cache for minutes afterwards.
`<the published hostname>` is now the only record in the zone, and anything else
reaching the tunnel gets `http_status:404`.

The tunnel is still **named** `discord-activity`. Renaming it would invalidate
the credentials file and every reference to the UUID while changing nothing
about what it serves.

### What is NOT done, said plainly

**`cloudflared` is not a service.** It was started by hand and nothing restarts
it, so a reboot takes the instance offline. `sudo cloudflared service install`
is the fix; it needs root and was not requested, so it was not done.

The abuse limits of GOAL step 8 are unchanged and still open. Access removes the
anonymous-stranger threat model, not the authenticated-operator one: D37's three
unscoped surfaces are answered by there being one trusted principal, which is a
hosting decision and not a repair.

See D37, D57, D60, D61, and `CONTINUE.md` §1 Decision 2, §7.

## D63 · What it took to be comfortable leaving this running — 2026-09-01

Published is not hosted. D62 put `<the published hostname>` behind Access; this is
what stood between that and something worth leaving up.

### The 502 was a recurring outage, and it is now impossible rather than watched

`origin_probe.sh` detected it. Detection was the wrong fix. The frontend's
generated `frappe.conf` declares `upstream backend-server { server backend:8000; }`
with **no `resolver`**, so nginx resolves that name once at config load and
caches the address for the life of the process — and every backend restart
handed it a new one. Two hours of 502 on 2026-08-31 came from a routine
restart.

`compose.custom.yaml` now pins `backend` to `172.21.0.10` and `websocket` to
`172.21.0.5` on a declared subnet. Verified the only way that counts: the
backend was restarted, kept its address, and nginx served **200 throughout**
with no reload. `frappe.conf` is regenerated from a template at every start, so
editing it in place is lost; bind-mounting a patched template would shadow the
image's own copy and go stale at the next upgrade. One line of addressing beats
both.

**Applying it cost an outage, which is the honest part.** Declaring `ipam` on a
network Docker had already created without one leaves it half-applied: the
websocket and both queues lost their addresses, `ENOTFOUND redis-queue`, and
the site went to 500. It needed a full `down`/`up` — volumes intact, checked
before and after — to take effect. A network change is not a hot edit.

### There were no backups. None.

Ninety-odd sales, real Sales Invoices booked against real testnet payments, and
the database in a Docker volume one `down -v` from gone. The chain still holds
the payments; *which sale a payment settled, which invoice it booked and what
an operator decided about a review* exist only here.

`backup.sh` runs daily under a systemd timer and verifies rather than hopes:
`bench backup` exited 0, the files exist and are non-empty, the gzip streams
pass `gzip -t`, the dump contains this app's tables — **and the live database's
newest sale appears in it**, which is what proves the dump is this database and
is current. Both failure paths were seen red: a truncated dump, and a
well-formed one missing that sale.

The first version counted `INSERT INTO` statements and reported "1 sale
insert statement(s)" against a database holding eighty. mysqldump packs
thousands of rows into one statement, so the number was true and meaningless.

It also captures the six files in `frappe_docker/` that **git cannot version** —
including `compose.custom.yaml`, which holds the loopback pin and the addresses
above. A database restored without them comes back exposed and flaky.

### Nothing watched it, and the first monitor I wrote lied twice

`health.sh`, every fifteen minutes: the published URL reaches the **Access**
login and not Frappe, the tunnel service is up, lingering is on, the origin
serves and refuses every non-loopback address, nine containers, the four
workers agree, no settled sale is missing its invoice, the scheduler ticked,
the booking sweep ran, backups are recent, disk headroom.

Two of those checks were wrong when written, and the negative controls are what
found it — not the checks passing:

1. It read `Scheduled Job Log` as a heartbeat and reported *"the scheduler has
   not run a job for 2706s — the booking sweep is not running"* on its first
   execution, while `cryptopos.watch.heartbeat` had run **seventeen seconds**
   earlier. Frappe writes that table only for jobs configured to log or ones
   that failed. `Scheduled Job Type.last_execution` updates on every run.
2. The corrected version then **passed** a `last_execution` seven hours in the
   future — the site runs `America/Adak` against a UTC database — because it
   tested only `< 600`. A negative age read as recent. In this repository, of
   all places: D19 was a timezone making every payment look late. A guard that
   reads an impossible time as healthy fails open on exactly that.

A monitor that cries wolf is worse than none, because it teaches you to ignore
it. Both directions are now asserted.

### Survives a reboot, and each part was checked separately

`cloudflared` was a process started by hand. It is now a **user** systemd
service — no root needed — with `loginctl enable-linger "$USER"`, which the user
was permitted to set for themselves, making it a boot service rather than a
login-session one. `Restart=always`, and proven: `kill -9` on the main PID
brought it back and the site answered within seconds. Docker is enabled, all
nine containers are `unless-stopped`, and both timers are enabled.

`health.sh` asserts lingering, because without it the unit file makes a promise
it does not keep.

### Capacity, which was the other half of the question

The published rail is `xtr` and visitors pay from their own wallets, so the
number that matters is whether the merchant can keep paying fees: **977.14 XTR
against 3,493 µT per call — about 279,000 calls.** Not a constraint.

`runway.py`'s headline says the instance can serve **0** more sales, and that is
about `pol`, which is not offered publicly and never was (§1 Decision 3). Its
`xtr` figure of 197 is the *dev-bench customer's* wallet, which a public
visitor does not use. Reading either as the published instance's capacity would
be wrong in opposite directions.

### What is still not true

`cloudflared` is a **user** service: if lingering is ever cleared, it silently
stops being a boot service. The health check watches for that.

**The journal is the alerting.** Nothing pages anybody. A failing check marks
the unit failed and writes to `journalctl --user -u cryptopos-health`; if
nobody looks, nobody knows.

**The backups are on the same disk as the thing they protect.** Better than
nothing, and not off-site.

**No backup has been restored.** The script says so itself and gives the
throwaway-site command. A backup nobody has restored is a rumour.

See D1, D2, D19, D31, D62.

### Addendum, same day: a recreate dropped a rail and the gate said OK

Applying the address pins needed a full `down`/`up`, and that removed the
Solana rail plugin from all four containers — it is `pip install`ed into them,
not baked into the image. **`rails_agree.sh` reported "all four processes drive
the same rails" the entire time**, because they agreed perfectly about a
capability none of them had. That gate asks about agreement; nothing asked
about capability.

The only thing that noticed was the app harness, which failed with `name
'cryptopos' is not defined` — an error naming neither pip nor the plugin.

`health.sh` now asks whether every ENABLED rail is driveable, and
`install_plugins.sh` is the recovery. It needs `--no-deps`: the plugin declares
`cryptopos-core>=1.1`, core arrives on PYTHONPATH from the bind mount rather
than through pip, and a plain install refuses to resolve its own dependency.

The durable fix is baking the plugins into the image so a recreate cannot drop
them. That is not done. Until it is, this is detection plus a one-command
recovery, which is honest but is not the same thing.

