# CryptoPoS — a Frappe app

A vertical slice of the tkinter CryptoPoS terminal, rewritten in Frappe idioms
and running against ERPNext. One rail (Bitcoin testnet4), the whole path:
charge → watch → bind → settle → book.

## What it proves

Two suites, both asserting rather than printing, each check naming the rule it
defends:

```bash
# sale path, against the live testnet4 chain — 31 checks
bench --site erp.localhost execute cryptopos.harness.run

# policy tier, against the live Ootle contract — 43 checks
bench --site erp.localhost execute cryptopos.harness_loyalty.run

# terminal render logic, no browser needed — 59 checks
node tests/terminal_render_test.js
```

## The terminal

`/app/terminal`, or the **Terminal** shortcut on the CryptoPoS workspace.

The keypad is the home — no gallery step and no product catalog in the charge
path. Digits, `.`, Backspace work from the real keyboard; Enter charges, polls,
or starts a new sale depending on where the sale is; Escape cancels.

Three audiences share the window and the default belongs to one of them:

| audience | gets | where |
|---|---|---|
| merchant | the terminal card, alone | on first open |
| customer | the awaiting screen and its QR | leaned over the counter |
| developer | the dev bench and the activity log | two checkboxes, **off by default** |

Hiding the log is only allowable because a disclosure may hide an explanation
and never a refusal — so an error raised while the log is closed is held and
painted on the terminal card itself (`note_error()`). Opening the log is what
clears it.

The QR is encoded server-side by the vendored `qrcodegen.py` and sent to the
browser as a **module grid, not markup**: Frappe's HTML sanitiser strips `d` and
`fill` from stored SVG, which yields a well-formed and completely blank image.
The browser draws the bits it is handed, so there is still exactly one encoder.

## The one design decision everything else follows from

**The sale is not a Sales Invoice.** ERPNext's `docstatus` has three values —
draft, submitted, cancelled. The terminal has eight states and four endings,
and the fourth ending is the point:

```
idle → awaiting → detected → confirming → confirmed (shown as SETTLED)
          |        (mempool)   (the gate)      ↘ failed
          ↘ expired (clean / part-paid)         ↘ needs_review
```

Mapping eight onto three would require deciding whether *"I cannot tell"* is a
submit or a cancel. It is neither. So `Crypto Sale` owns the state machine, and
a Sales Invoice is **emitted** from it once the sale is settled, bound and real
— never before, and never for the other three endings.

That seam is `settle.book()`, and it only opens one way.

## The distinctions the port had to keep

| | |
|---|---|
| `credited_native` | money that arrived **and** can be tied to this sale. The only figure that books. |
| `sighted_native` | money that arrived and cannot be tied to this sale. Displays, never books. |
| `end_kind = unverified` | the question could not be asked at all. Not the same as "unpaid". |

A heartbeat that fails is not a heartbeat that found nothing. When the final
look never reaches the chain, the sale parks as NEEDS REVIEW saying *could not
verify* — because "expired, unpaid" is a claim about the world that the
observation did not support.

Native amounts are stored as **strings**, not integers: wei overflows bigint,
and the amount is the single source of truth that the URI, the display and
every tolerance check derive from.

## The policy tier (Ootle loyalty)

Points accrue against sales on a Tari Ootle contract that makes devaluation
structurally impossible. **EARNING ONLY** — spending does not work, and the
surface says so everywhere it mentions points.

### Why ERPNext's own Loyalty Program is deliberately NOT used

This was the decisive design finding, and it has two independent halves.

**It would restore devaluation.** ERPNext stores only a point *count* on
`Loyalty Point Entry`; the monetary value is computed fresh at redemption from
the live `Loyalty Program.conversion_factor` — one editable Float, no per-entry
snapshot. Change it and every point ever earned is retroactively revalued. That
is exactly the airline-miles scandal the contract exists to make impossible.

**It would claim points are spendable.** Creating `Loyalty Point Entry` rows
lights up ERPNext's redemption UI — a cashier could apply a discount and post GL
entries against points that on chain cannot move at all (`withdraw: DenyAll`,
`Locked`; enrolment blocked on a co-signing wallet that does not exist). The
system would assert the one thing the operator is explicitly told never to say.

So the chain is the ledger, `Crypto Loyalty Award` is the local mirror, and
ERPNext's loyalty tables are left alone. The harness asserts that no
`Loyalty Point Entry` is ever created and no emitted Sales Invoice carries a
loyalty programme.

### What the contract guarantees, read live

Verified on-chain against component `component_73f1d0bf…` (K1, version 36) —
all four resource slots match `ootle-testnet/ADDRESSES.md`, which is what fixes
the positional mapping of its CBOR state:

| | |
|---|---|
| redemption rate | **100** points per cent — no setter exists |
| per-award ceiling | **1,000,000** points — tightens only |
| per-epoch ceiling | **10,000,000** points — tightens only |
| `owner_rule` | `None` — no owner, no upgrade path |
| `burn` / `withdraw` / `freeze` | `DenyAll`, updaters `Locked` |

### The split: reads in the container, writes on the host

**Reads** go straight from the Frappe container to the indexer — free, keyless,
feeless, and proven working. That is what makes the promise checkable by the
customer, from any machine, at no cost.

**Writes cannot happen in the container**, and that turned out to be the right
architecture rather than an obstacle. The toolkit is dynamically linked against
a newer glibc than the frappe image carries:

```
/tmp/toolkit: /lib/x86_64-linux-gnu/libc.so.6: version `GLIBC_2.38' not found
```

So the container holds *intent* and the host holds the *key*:

```
settled sale → frappe.enqueue → Crypto Loyalty Award (pending)
                                       ↓  claim_awards
                          bin/award_drainer.py  (host, has the key)
                                       ↓  toolkit loyalty award …
                                       ↓  report_award
                              Crypto Loyalty Award (issued|refused|unverified)
```

Nothing in the web application can mint. Running the drainer:

```bash
export CRYPTOPOS_URL=http://localhost:8080
export CRYPTOPOS_KEY=… CRYPTOPOS_SECRET=…
./bin/award_drainer.py --dry-run --once     # show what would be written
./bin/award_drainer.py                      # loop
```

### The four award states

`pending` · `issued` · `refused` · `unverified` · `not_offered`

`unverified` means the attempt did not confirm in time and **may still have
landed** — the record refuses to say either way. That is deliberate
under-claiming: a customer told they hold nothing who turns out to hold
something is pleased; the reverse is a broken promise. The drainer also guards
the case where the toolkit exits 0 on a *submitted but rejected* transaction, so
success requires `Commit` present and `Reject` absent, never the exit code.

An award is never retried after submission — a second submission is a second
mint, and nothing on this contract can burn the duplicate.

## Doctypes

| DocType | Role |
|---|---|
| `Crypto Sale` | the state machine's memory; everything snapshotted once by `charge()` |
| `Crypto Sale Event` | append-only transition log, each row attributed to the transport that caused it |
| `Crypto Rail` | one row per chain/asset, carrying its settle gate in words |
| `CryptoPoS Settings` | merchant config, and what a settled sale books into |

## Modules

| File | Role |
|---|---|
| `charge.py` | the one write that decides what a sale is |
| `watch.py` | the heartbeat; `ChainUnreachable` is a distinct outcome, not an error to swallow |
| `settle.py` | the ERPNext seam |
| `rates.py` | a rate is a number, a source, and a time |
| `api.py` | whitelisted endpoints for a till surface |
| `harness.py` | the end-to-end proof |

## Running it

The heartbeat is a `scheduler_events` cron entry (`* * * * *`), so sales settle
whether or not anyone is looking at a screen. `cryptopos.api.poll` single-steps
the same function, so the timer and the button cannot drift apart.

```
GET  /api/method/cryptopos.api.rails
POST /api/method/cryptopos.api.charge   {usd_cents, rail_key}
GET  /api/method/cryptopos.api.status?sale_name=CPS-2026-00001
POST /api/method/cryptopos.api.poll     {sale_name}
```

## Taking a real testnet4 payment

Where the money goes is **CryptoPoS Settings → Bitcoin testnet4 address**, and
`charge()` refuses outright rather than watching an address nobody holds the
keys to. The configured address is derived from `BTC_MERCHANT_XPUB` at index
1000 — high on purpose, so it cannot collide with the tkinter terminal, which
spends indices from 0 upward one per sale.

Four things decide whether a real payment settles, and three of them are
decided *before* you press Charge:

1. **Have the coin first.** The rate lock is 15 minutes
   (`charge.RATE_LOCK_SECONDS`) and a sale that reaches the end of it having
   seen nothing goes to `expired`. Getting from a faucet to a broadcast inside
   that window is the tight part; funding the sending wallet beforehand makes
   it easy. Once the payment is in the mempool the sale is `detected` and the
   lock no longer ends it, so only the *gap before broadcast* is on the clock.
2. **Pay at least the invoiced amount.** Binding is `credit >= invoiced_native`
   in satoshi. Underpaying is recorded as *sighted* — real money that could not
   be tied to the sale — and never books. Overpaying settles and is stamped
   `end_kind=over`.
3. **One sale per amount at a time.** The address is shared and the binding is
   by amount inside the window, which is the weakest binding this terminal
   offers and the sale says so. Two open sales for the same amount are not
   distinguishable; the first to bind wins and the second parks.
4. **The gate is 1 confirmation on testnet4** (3 on mainnet). At testnet4 block
   times expect a few minutes, and the heartbeat is what notices.

Watch it land without touching the screen:

```bash
bench --site erp.localhost execute cryptopos.watch.heartbeat
```

The terminal's **Poll the node** button calls the same function, so nothing
about the timing depends on which one you use.

## Development loop

The app is bind-mounted from the host by `compose.custom.yaml`. The image bakes
`apps/` into a layer and only `sites` and `logs` are volumes, so an editable
install (`pip install -e apps/cryptopos`) lands in **one container's** writable
layer and is lost the moment that container is recreated. That failure is
quiet in the worst way: the backend keeps serving, so the terminal looks fine,
while the scheduler cannot import `cryptopos.watch.heartbeat` and the long
queue cannot import `cryptopos.loyalty.award_for_settled_sale`. Sales then
only advance when somebody presses **Poll the node**, and no award is ever
written — with nothing on any screen saying so.

So the path is declared instead of installed. Three stanzas in
`compose.custom.yaml` carry it, and none of them need repeating after a
recreate:

| what | where | why |
|---|---|---|
| `PYTHONPATH=/home/frappe/frappe-bench/apps/cryptopos` | `backend`, `scheduler`, `queue-short`, `queue-long` | every service that executes app code can import it |
| bind of `cryptopos/public` → `frappe-bench/assets/cryptopos` | `frontend` | `sites/assets` is a symlink to a path *outside* the sites volume, so nginx never sees what `bench build` links in the backend |
| `"scheduler_tick_interval": 60` in `common_site_config.json` | bench-wide | Frappe evaluates cron on its scheduler tick, which defaults to **four minutes** — the `* * * * *` heartbeat is otherwise a four-minute poll |

For deployment, add cryptopos to `apps.json` so it bakes into the image like
erpnext and hrms; the first two rows above then stop being necessary.

## What this slice does NOT do

Stated because a rail that says "works" and a rail that says "partial" make
different promises, and the port should not upgrade one on the way across.

- **One rail.** BTC only. The other rails' watchers (EVM, Solana, Monero, Zcash)
  are not ported.
- **Binding is by amount on a shared address.** No HD derivation from an xpub
  yet, so two sales for the same amount inside one window are distinguishable
  only by which binds first. The loser parks rather than double-books; that is
  handled, but per-sale addresses are the real fix.
- **No award has been minted through this path.** The queue, the drainer, the
  argv and the reporting are all proven, but only against a harness account
  that does not exist on chain. A real mint to a real account has not been run,
  so `issued` is the one award state never observed end to end.
- **Enrolment and redemption are absent, not unfinished.** Both need the
  customer's signature on the merchant's transaction, and no wallet in reach can
  co-sign. That is R2, an external dependency; no work in this tree closes it.
- **Contract 3 (return entitlements) is not wired.** It rides the same
  component and the same republish, but `issue_entitlement` has no call site
  here — refund and warranty lines remain local records only.
- **No receipt.** `receipt.py` and its signed region are not ported, so the
  terminal shows an ending but hands the customer nothing.
- **The terminal has not been driven in a real browser.** The render logic is
  covered by `tests/terminal_render_test.js` against a stubbed desk, and the
  page is served correctly by `frappe.desk.desk_page.getpage`, but no one has
  clicked it. Layout, focus behaviour and the auto-poll timer are unproven.
- **No `drive_gui.py` equivalent.** The tkinter tree measures its own claims
  about what is visible; nothing here does that yet.
- **Mainnet refuses**, by decision, as it does in the original.
