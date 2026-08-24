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
intervals *sit*, not an outlier. **Re-run it rather than believing this table:**
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

Closing that means settling on finality. So the number that matters is
time-to-finality, and it is not 12 seconds. Measured live on Sepolia,
2026-08-24:

| gate | lag behind tip | against the 15-minute lock |
|---|---|---|
| 3 confirmations (what runs today) | ~36 s | fits — and is reorg-unsafe |
| `safe` (justified checkpoint) | **10.8 min** | fits, leaving ~4 min for the customer to act |
| `finalized` | **17.2 min** | **does not fit** |

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
