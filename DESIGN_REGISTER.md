# DESIGN_REGISTER.md — cart, metrics, loyalty page, settings page

**Status: PROPOSAL, not decided.** Written 2026-08-17 from a four-agent research
pass over Frappe 16.31.0 / ERPNext 16.32.1 source, verified against the live
`erp.localhost` site. Section 6 lists the decisions that are the maintainer's and are not
taken here.

**Revision 3 — after a second adversarial review, against the framework.** It
found an arithmetic bug the ethos review missed: the book-time comparison as
originally written fails for **9.2% of cent values** (§1.3(d)), which would have
taken crypto and then never booked, on about one sale in eleven. It also
established that the preview must call `set_missing_values` to match what books,
and it **overturned Revision 2's claim** that change 4 would destroy evidence —
the mechanism does not reach read-only fields holding empty values. §2.4 now
records the retraction. Change 4 stays dropped, for weaker reasons.

**Revision 2 — after adversarial review against the project's own ethos.**
Two proposals were **withdrawn** (§2.3 changes 3 and 4; change 4 would have
destroyed the provenance of the 12 could-not-verify sales — see §2.4), one
justification was found unsupported and removed, and §1.3 gained three
constraints it was silently missing: snapshot the tax rather than re-deriving it,
build both invoices identically, and wrap the calculation so a ledger
misconfiguration cannot refuse a payment. §1.2's claim to be "the same promise
with the arrow reversed" was false and is now stated as the weaker invariant it
is. A second review, against the framework rather than the ethos, is still
outstanding.

---

## 0. The finding that reframes everything

**ERPNext v16 turned `POS Invoice` off by default.** `POS Settings.invoice_type`
defaults to `"Sales Invoice"` (`pos_settings.py:24`), and a non-return POS
Invoice saved while that setting holds throws outright:
`pos_invoice.py:466-469` — *"Sales Invoice mode is activated in POS. Please
create Sales Invoice instead."*

So "emit a plain submitted Sales Invoice" is not a deviation from ERPNext. **It
is what v16's own POS does.** The 2026-08-15 hybrid-state decision aged into
alignment rather than away from it.

The reason that decision was made also re-confirms independently: `POS
Invoice.docstatus` is the stock 3-value integer, and its only other axis,
`status`, is `read_only: 1` and unconditionally recomputed by `set_status()` on
every `validate()` (`pos_invoice.py:599-657`, called at `:226` and `:251`). A
`needs_review` value written there is destroyed on the next save. `Crypto Sale`
must stay sovereign.

---

## 1. The cart

### 1.1 What exists, and the invariant it protects

The tkinter terminal already has priced line items (the maintainer, 2026-07-27), gated
behind `merchant.itemised()`. A line is
`{qty, desc, cents (EXTENDED — qty already applied), serial?}` — free text, no
catalog. `add_basket_line()` refuses a serial unless `qty == 1`, because a
serial identifies one object and contract 4 binds warranty cover to it.

The governing rule is `pos_actions.py:337-352`, and it runs **opposite to a
normal register**:

> THE BASKET MUST MAKE THE TOTAL. […] Once the sale is charged the customer has
> been quoted a crypto amount against `usd_cents`, and a receipt printing lines
> that add to something else would be showing them two different prices for one
> purchase.

The keypad owns the total; lines are checked *against* it and a mismatch blocks
the charge. `receipt._item_lines` re-checks the sum a second time at print, on
the grounds that "printing the lines under a total they do not make would be the
receipt asserting arithmetic it did not do."

**Taxes break this.** A computed tax means the grand total is derived, not
typed. Keypad-authoritative and tax-inclusive cannot both hold.

### 1.2 Proposal — preserve the invariant by inverting the check, not dropping it

Two modes, both explicit on screen:

| mode | who owns the total | tax | lines |
|---|---|---|---|
| **Quick sale** (default, ships today) | the keypad | none | none |
| **Itemised sale** (gated, opt-in) | the cart | computed | required |

In itemised mode the keypad stops meaning "the total" and starts meaning "this
line's price". `usd_cents` remains the single authoritative integer the crypto
amount is quoted against — it is now *derived* rather than typed, and the
invariant becomes:

> `usd_cents` must equal the grand total that ERPNext will compute for these
> exact lines — checked at charge, and re-checked at book.

**This is a weaker invariant than today's, and calling it "the same promise with
the arrow reversed" would be false.** Today's check compares two *independently
produced* numbers — a human typed the total, a human typed the lines — and
agreement of two independent derivations is evidence, which is why it can catch a
real mis-ring. Once `usd_cents := f(lines)`, the charge-time comparison is
`f(lines) == f(lines)` and cannot fail. What survives is the book-time check,
which fires only when the *world* changed, never when the operator erred.

Two consequences that must be designed for, not waved at:

- **The mis-ring guard is gone and needs replacing** — most likely a confirm step
  showing lines and total together before the crypto amount is quoted.
- **`receipt._item_lines` (`receipt.py:451-455`) breaks.** It prints *"lines do
  not sum to the total"* whenever `sum(cents) != usd_cents`. With tax that is
  **every itemised sale**, turning an honesty line into constant noise. It must
  learn about tax, or itemised sales must carry a tax line it can see. (The
  cryptopos slice ships no receipt yet — this lands when one arrives.)

### 1.3 The mechanism — ERPNext's calculator as a pure function

The hard problem is that CryptoPoS must know the taxed total **at charge time**,
while `settle.py` is explicit that only a settled, bound, real sale may produce
an invoice — *"Every other sale […] produces nothing, and that silence is the
design rather than a gap in it."*

Computing tax independently is not an option. ERPNext accumulates per-item tax
**unrounded** and rounds once at the end
(`taxes_and_totals.py:463`, `:680-689`); a per-line integer engine disagrees on
most multi-line carts. Measured: 100 lines × $0.03 @ 6.25% → ERPNext `0.19`,
per-line rounding `0.00`.

**Resolution: run ERPNext's own calculator in memory and persist nothing.**

```python
si = frappe.new_doc("Sales Invoice")          # never inserted
si.customer, si.company, si.currency = ...
si.conversion_rate = 1.0                      # S1: required once currency != company currency
for line in lines: si.append("items", {...})

# get_taxes_and_charges returns None (not []) for a blank master name
# (accounts_controller.py:3221) -- iterating it raises TypeError.
si.extend("taxes", get_taxes_and_charges(MASTER, template) or [])

si.run_method("set_missing_values")           # NOT optional -- see below
si.run_method("calculate_taxes_and_totals")
total = si.rounded_total or si.grand_total    # authoritative, per (c)
```

**`set_missing_values` is what makes the preview match the booking.** All ~25
in-tree callers that price an uninserted doc run the pair in this order
(`quotation.py:429-430`, `sales_invoice.py:2511`, `sales_order.py:1195`, …), and
`AccountsController.validate` runs the same pair (`:273-274`, `:307-308`) — so
the booked invoice always passes through it and a preview that skips it is
comparing two different computations. It sets `item_tax_rate` from
`get_item_details` unconditionally for non-returns
(`accounts_controller.py:1135-1138`) and applies pricing rules, either of which
can move the total. On this site it happens to change nothing for
`CRYPTOPOS-SALE` — which has no Item Tax Template — but three Item Tax Templates
already exist, so the day §6.2 goes the catalog way, the preview and the booking
diverge silently.

This creates no document and leaves the "settled sales only" rule untouched.

**Measured 2026-08-17** against the live site's default `US ST 6% - T` template.
Each cart was priced twice — once in memory, once inserted and submitted — and
compared, then rolled back (0 rows leaked, confirmed on a fresh connection):

| cart | in-memory | submitted | naive per-line |
|---|---|---|---|
| 1 × $9.99 | 10.59 | 10.59 | 10.59 |
| 3 × $9.99 | 31.77 | 31.77 | 31.77 |
| 100 × $0.03 | 3.18 | 3.18 | **3.00** |
| 4 mixed odd-cent lines | 50.53 | 50.53 | **50.52** |
| 7 × $2.35 + 13 × $0.99 | 31.08 | 31.08 | 31.08 |

**In-memory equals submitted on every cart.** The third column is why the
calculator must be borrowed rather than reimplemented: a naive per-line integer
engine diverges on 2 of 5 realistic carts, by 18¢ and by 1¢. Both divergences
are silent.

*Scope of that result:* both sides were built identically, taxes pre-appended,
and neither called `set_missing_values`. It establishes "same construction → same
total" — constraint (b) — not that any invoice built by any route will agree.
It must be re-run once (b) and `set_missing_values` are wired in.

One more setting silently governs the equality: `Accounts Settings.round_row_wise_tax`
is `0` here, which is why tax accumulates unrounded (`taxes_and_totals.py:449-451`).
Flipping it changes the arithmetic on both sides — harmless if they stay
symmetric, but it belongs on the list of things that invalidate the measurement.

**The failure surface, which is the part this section originally hid.** The
snippet has no `try`, and `calculate_taxes_and_totals` throws on: a missing
company (`taxes_and_totals.py:33-40`), a deleted or renamed item
(`:99`), a deleted Item Tax Template (`get_item_details.py:942`), and four
`validate_taxes_and_charges` paths (`accounts_controller.py:3254-3273`).
`charge.py` already throws in five places — but every one is a *payment* refusal
("we cannot take this payment honestly"). A throw out of the tax engine is the
**ledger** refusing at the counter, which is `CHARTER.md:54` — *"a sale must
never fail because the policy layer is down"* — and the same sentence
`settle.py:41-43` uses to justify not throwing on the settle side.

So the calculation is wrapped, following `loyalty.request_award`
(`loyalty.py:72-86`), which is total by construction and never raises. On any
exception the terminal **degrades to quick sale** — keypad-authoritative, no tax,
no lines — with the reason stated on screen. It refuses the itemised *mode*,
never the sale.

**Three constraints, all found by adversarial review and none optional.**

**(a) The tax must be SNAPSHOTTED, not re-derived.** `charge.py:1-9` is the
doctrine: everything but state, watcher discoveries and scratchpad is written
once, because "an earn rate changed for a promotion" must not reach backwards
into a sale in flight. A tax rate changed for a rules update is the same class of
thing. Storing only a `Link` to `item_tax_template` and re-running the calculator
at settle leaves that field live — and the window is not the 15-minute rate lock,
because a sale in `detected`/`confirming` can sit indefinitely. So charge stores
the **resolved tax rows** (account_head, rate, charge_type, `item_tax_rate` JSON)
onto the sale, and settle rebuilds from the snapshot with
`calculate(ignore_tax_template_validation=True)`. The book-time check is then a
cross-check, not a re-derivation.

**(b) Both sides must build the invoice the same way.** `set_taxes_and_charges`
returns early when `taxes` is already populated (`accounts_controller.py:1293`),
so pre-appending taxes suppresses both `append_taxes_from_master` and
`append_taxes_from_item_tax_template` — and `add_taxes_from_item_tax_template` is
**on** (`= 1`) on this site. Symmetric construction is what makes the measured
equality below hold; it is a design requirement, not a property that comes free.
`settle.book()` today appends no taxes at all and must be changed in step.

**(c) One authoritative figure: `rounded_total or grand_total`**, matching
`calculate_outstanding_amount` (`taxes_and_totals.py:1036`) — used at charge, at
book, and on screen. On USD these are equal; on a currency where they are not,
quoting one and checking the other would make **every** sale refuse to book.

**(d) The comparison must round, not multiply-and-equate.** The obvious form —
`flt(total, 2) * 100 == sale.usd_cents` — is a float product compared to an int,
and it is wrong for **18,351 of the first 200,000 cent values (9.2%)**, measured.
The first failures are $0.07, $0.14, $0.28, $0.55, $1.09. A sale for any of those
would take crypto, settle, and then refuse to book forever — the exact outcome
this check exists to prevent, on roughly one sale in eleven. Use
`round(flt(total, 2) * 100)`, or `int(Decimal(str(total)).scaleb(2))`;
`charge.py:13` already imports `Decimal`.

At settle, `settle.book()` rebuilds from the snapshot and compares the total.
On mismatch it refuses and records the refusal — but **the refusal must reach the
screen**, which today it would not: `may_book()` has four terms
(`crypto_sale.py:133-152`) and none is "the totals agree", so a settled sale
failing this check returns `(True, "")` and `terminal.js:398` renders
*"Not booked — "* with nothing after the dash. So `may_book()` gains the totals
term, and the check is an `if` with a recorded refusal, never a bare `assert`
(stripped under `python -O`).

This is **not** the same shape as today's missing-configuration refusal, and the
difference matters: that one fires on every sale until fixed and is repaired by
filling a setting; this one fires on a single sale, intermittently, and its
remedy is reverting a template edit nobody recorded.

### 1.4 Display rules, forced by the framework

- **Render the total verbatim. Never `subtotal + tax`.** ERPNext's
  `grand_total_diff` (`taxes_and_totals.py:707-736`) is transient and never
  persisted, so the printed sum cannot always be reconstructed from stored
  fields. It fires **only when some tax row is `included_in_print_rate`** — the
  block is gated on exactly that — which is why my four exclusive-tax test cases
  all showed `+0.00`. That was structural, not luck. The rule still costs nothing
  and removes the class, and tax-inclusive pricing is a normal merchant choice.
- **One authoritative figure everywhere: `rounded_total or grand_total`** — see
  §1.3(c).
- Cart row shows extended amount, matching the existing `cents`-is-extended rule.

### 1.5 New doctype

`Crypto Sale Line` (child of `Crypto Sale`): `item_code` (Link→Item, optional),
`description` (Data, reqd), `qty` (**Float**, reqd — matching
`Sales Invoice Item.qty`; an Int cannot express 0.5 kg or 1.5 hours and nothing
here needs it to, since `amount_cents` carries the money), `rate_cents` (Int, length 20),
`amount_cents` (Int, length 20), `serial_no` (Data), `item_tax_template`
(Link, optional) **plus the resolved rate snapshot** required by §1.3(a).

**`amount_cents` wins.** Storing both a unit rate and an extended amount
reintroduces the redundancy `pos_actions.py:4097-4100` deliberately removed —
*"storing a unit price and multiplying at print time would let the receipt's
arithmetic disagree with the ledger's."* `rate_cents` is kept only because
ERPNext needs a `rate` to build the invoice line; where they disagree,
`amount_cents` is the sale's figure and the disagreement is a refusal, not a
silent re-derivation.

### 1.6 What is NOT proposed

`is_created_using_pos = 1` is **rejected**. It buys the POS Closing Entry
drawer-reconciliation sheet, and costs: a mandatory `Open` POS Opening Entry
dated today (`sales_invoice.py:1231-1258`, three separate throws), mandatory
`pos_profile`, full-payment enforcement, and a cancel lock. Every one of those
is a foreign lifecycle imposed on a doctype that already owns its own.

Stated honestly, the thing given up has no cheap substitute: POS Closing Entry
reconciles **counted cash against system figures per mode of payment**, and a
Script Report over `Crypto Sale` yields sales, not a drawer count. If drawer
reconciliation is ever wanted it is a new child table and a real design, not a
report — but a watch-only crypto terminal has no drawer to count, which is why
the trade looks right here and would not on a cash till.

---

## 2. Metrics Frappe recognises

### 2.1 The blocking defect

`Long Int` is in Python's `numeric_fieldtypes` (`frappe/model/__init__.py:78`)
and **absent from JavaScript's** (`model.js:140`). Every aggregation picker in
the Desk is client-side. So `usd_cents` and `rate_microcents` aggregate
perfectly on the server and **cannot be selected in any chart, number card,
report-view total, or group-by dialog.**

Worse, a hand-written fixture naming them is unstable: `select.js:55-59`
silently rewrites a Select whose stored value is not in the rendered options to
the first available one. Today the only Int-family field on `Crypto Sale` is
`loyalty_earn_rate` — so a `usd_cents` chart silently becomes a
`loyalty_earn_rate` chart the first time anyone opens it.

### 2.2 The fix, and it is free

`schema.py:433-435` — a DocField of `"fieldtype": "Int", "length": 20` is
created as a **`Long Int` column** while remaining `Int` to every client picker.
`File.file_size` ships exactly this shape (`file.json:81-85` → `bigint(20)`).

**On this site `usd_cents` and `rate_microcents` are already `bigint(20)`.**
Flipping the fieldtype is therefore a pure metadata change with **no column
migration at all** — same storage, same 64-bit range, same integer exactness.

### 2.3 Changes

| # | Change | Why |
|---|---|---|
| 1 | `usd_cents`, `rate_microcents`: `Long Int` → `Int` + `length: 20` | makes them chartable; zero migration |
| 2 | `search_index: 1` on `charged_at`, `state`, `rail_key` | only two indexes exist today (`PRIMARY`, `creation`); Links are **not** auto-indexed in v16 |
| 3 | ~~Add `credited_units_e8` / `invoiced_units_e8`~~ **WITHDRAWN.** Use the Script Report path below. A stored lossy twin of the only figure that books buys nothing the Script Report does not, makes the *convenient* path the inexact one, and `_e8` is exact only for BTC — it would truncate ten places on an 18-decimal rail, invisibly. Still true and still worth stating: **`SUM()` over the `Data` column silently coerces and returns garbage for wei — never chart it.** |
| 4 | ~~Give `end_kind`, `provenance`, `identity_source`, `binding` a real first option instead of `""`~~ **WITHDRAWN — see §2.4** | — |
| 5 | `title_field: "label"` on `Crypto Rail` | rail-grouped axes read "Bitcoin / BTC" instead of `btc` |
| 6 | Ship `crypto_sale_dashboard.py` | connections bar is empty today; `links: []` |
| 7 | Ship Dashboard Chart + Number Card fixtures with `is_public: 1` | they then appear at `/desk/crypto-sale/view/dashboard` with **no workspace wiring** (`dashboard_view.js:96-113`) |

Anything needing wei-exactness in a chart uses a Script Report +
`chart_type: "Report"`, which bypasses every fieldtype picker — **but such a
chart will not appear on `/desk/crypto-sale/view/dashboard`.** That view filters
`chart_type in ["Count","Sum","Group By"]` (`dashboard_view.js:96-104`), so a
Report chart must be placed on the Workspace instead. Change 7's zero-wiring
payoff applies only to the native chart types; the exact ones cost a workspace
row and a `content` block.

### 2.4 Withdrawn: renaming the empty Select options

The original change 4 proposed giving `end_kind`, `provenance`, `identity_source`
and `binding` a named first option in place of `""`. **It is withdrawn on two
counts, and the second is serious.**

**The justification did not hold.** `ignore_ifnull` is consumed in
*filter-condition* building (`db_query.py:1006`), not in grouping.
`get_group_by_chart_config` applies no filter to the group-by field. Empty
values are not dropped — they group into a bucket with a blank label. That is a
legibility problem, not silent loss, and it does not license the remedy.

**It also aims at a target that mostly isn't there.** Measured on the live site:

| field | `''` rows |
|---|---|
| `end_kind` | **0** |
| `identity_source` | **0** |
| `provenance` | 12 (all `state=needs_review`, `end_kind=unverified`) |
| `binding` | 40 |

Two of the four fields have no empty rows at all. The change would rewrite 52
historical audit rows to relabel a blank axis tick.

**A retracted claim.** Revision 2 of this document asserted that change 4 would
*destroy* those 12 rows via the `select.js:55-59` rewrite cited in §2.1. **That
was wrong.** The guard is `if (value && input_value && value !== input_value)` —
an empty stored value is falsy, so it never fires on exactly the rows in
question; and all four fields are `read_only: 1`, so no `$input` is built and
`input_value` stays `""` regardless. The trap is real for a *non-empty* value
absent from the options, which is the §2.1 case; it does not reach here.

So change 4 is dropped for being unnecessary and mis-argued, not for being
dangerous. If blank buckets are worth fixing, relabel at render time. If a real
value is wanted, **keep `""` in the options**, add the named value, and ship a
`post_model_sync` patch (`patches.txt` is currently empty) — and the choice of
word is a vocabulary decision about the sale, so it belongs in §6. `charge.py:116`
is explicit that the empty provenance is load-bearing: *"Presuming REAL here
would be the exact overclaim this field exists to prevent."*

---

## 3. The loyalty page

### 3.1 There is nothing to copy

**ERPNext has no UI anywhere that says "you earned N points on this sale."**
Points are created silently in `make_loyalty_point_entry` on submit
(`sales_invoice.py:542`). Every transactional loyalty surface in ERPNext is a
**redemption** surface. Its density is not a template — the thing CryptoPoS
needs to show is precisely the thing ERPNext never shows.

### 3.2 Actively dangerous to reuse

ERPNext loyalty redemption **is not a discount — it is a tender.**
`taxes_and_totals.py:1107-1109`:

```python
if self.doc.redeem_loyalty_points and self.doc.loyalty_amount:
    base_paid_amount += self.doc.loyalty_amount
    paid_amount     += self.doc.loyalty_amount / flt(self.doc.conversion_rate)
```

It adds to `paid_amount`, reduces `outstanding_amount`, and posts
Dr `loyalty_redemption_account` / Cr `debit_to` (`sales_invoice.py:1838-1870`).
**Reusing `loyalty_amount` "just for display" would silently short-pay the
invoice**, because that read is unconditional whenever `redeem_loyalty_points`
is truthy. Never populate either field.

Do not port: the payment-grid loyalty tile (`pos_payment.js:601-610`), any
"redeem" wording, `conversion_factor` ("1 Loyalty Point = how much currency"),
a `Currency`-typed points figure, or an expiry column. A currency figure beside
points is what implies redeemability.

### 3.3 What the page shows, top to bottom

1. **Header** — the earning-only notice first, not as a footnote.
2. **Balance** — read-only **Data**, never Int, never Currency, never with a
   fiat equivalent, and placed **outside the totals column** (§3.2's own rule: a
   currency figure beside points is what implies redeemability). Three further
   constraints: `ootle.points_balance` says *"NEVER call this on the path of a
   sale"* (`ootle.py:153-182`, two blocking indexer GETs), so it is fetched off
   that path; a balance of 0 and an unreadable balance are different answers, so
   when `balance is None` the page renders `balance_reason` in the degraded
   wording rather than `0` or blank — collapsing those two would be ethos rule 1
   violated on the verifiability axis, having been so careful on the
   redeemability one. `api.loyalty_status` already returns them separately
   (`api.py:178-194`).
3. **This sale earned** — renders `award.wording()` **and nothing else**. Not
   `loyalty.points_for`, which is `rate × usd_cents` with no award-state check
   (`loyalty.py:41-52`). At the moment this page draws, the award is `pending` at
   best — it is enqueued on the long queue (`watch.py:273-278`) and can still end
   `refused` for six reasons. A heading reading "This sale earned" above a bare
   number **is** the HOLDS claim, and `crypto_loyalty_award.claims_points()` is
   explicit that "only an issued award may be described to a customer as held."
   Putting queue state lower down does not repair a claim made higher up.
4. **Ceilings** — per-award and per-epoch, shipped beside the feature that
   offers them, per the existing rule.
5. **Check it yourself** — the indexer URLs already produced by
   `ootle.check_it_yourself()`.
6. **Award queue state** — pending / issued / refused / unverified, with the
   degraded wording as default and only a committed mint upgrading it.

`terminal.js:419-474` `loyalty_html()` already does most of this correctly.
Safe patterns worth borrowing from ERPNext: the collapsible-section-gated-on-
enrolment shape (`customer.json:544-550`), the read-only Data control
(`pos_item_cart.js:968-973`), and the HTML-notes block (`loyalty_program.js:8-44`).

---

## 4. CryptoPoS Settings

At **20 fields (13 non-layout)**, tabs are not warranted — POS Settings runs 6
fields flat and Selling Settings only tabs at 56. The observed threshold is ~50.

| # | Change | Why |
|---|---|---|
| 1 | **Move `loyalty_enabled` above `section_loyalty` first**, then put `depends_on: "loyalty_enabled"` on the Section Break | the master-toggle pattern (`stock_settings.json`) keeps the toggle *outside* the section it gates. `loyalty_enabled` is currently field 8, inside `section_loyalty` at 7 — gating the section as-is would hide the checkbox that controls it. Today the loyalty apparatus renders in full even when earning is off: "looks configured when it isn't" |
| 2 | Move `loyalty_earn_rate` out of the top section into the loyalty section | one concept currently split across two sections |
| 3 | Add `cryptopos_settings.js` (**none exists today**). On `mode → mainnet`, show a **non-dismissable explanation that the mode is refused, with no affirmative branch** — *not* `frappe.confirm` | **Corrected after review.** The ERPNext idiom (`stock_settings.js:98-118`, `system_settings.js:65-80`) has a no-op yes-branch, i.e. the change *succeeds* — it means "dangerous but permitted". Mainnet on a payment rail is **not permitted**: `CHARTER.md:222-227` scopes the 2026-08-15 exception to the Ootle layer and explicitly keeps the payment rails inside the boundary. Borrowing the permitted-but-risky idiom would tell the operator the opposite. Note also a JS guard is bypassed by `frappe.client.set_value`, so this is an explanation, never *the* gate — the gate stays `charge.py:67` |
| 3b | `description` on `chain_reference`, `company`, `cost_center` | the only three non-layout fields lacking one (10 of 13 already have it) |
| 4 | `frm.dashboard.add_indicator()` in `refresh` for indexer reachability / award-queue depth | Singles **cannot** show Connections meaningfully — `dashboard.js:391-406` filters on `doc.name`, which for a Single is the doctype name. Indicators and charts work fine. |
| 5 | `documentation_url` on 3–5 fields, pointed at in-app routes | `accounts_settings.json:599` uses `/app/pegged-currencies/...`; this is the "connected records" affordance for a Single |

Existing `cryptopos_settings.json:46` — *"Mainnet is a non-working mode by
decision. Selecting it produces a refusal at charge, which is the intended
behaviour."* — is already better written than most ERPNext descriptions. Keep
that voice.

---

## 5. Also found, unrelated to the above

- `terminal.js` does all its work in `on_page_load` with no `on_page_show`, so
  navigating away and back does not re-initialise. The modern shape splits
  chrome (`on_page_load`) from render (`on_page_show`).
- `$(document).on("keydown.cryptopos", …)` at `:203` is never unbound. A
  `:visible` guard at `:186` limits the damage but the handler still
  accumulates.
- `render()` re-writes `this.$body.html(...)` and re-binds every handler on
  every keypress. Cheap at 12 keys; destroys focus and scroll position once a
  scrollable cart exists. ERPNext's `toggle_component(show)` over persistent DOM
  is the pattern to move to.
- `add_to_apps_screen` and the workspace are fine; `/app/sales-invoice` at
  `:399` was corrected to `/desk/` on 2026-08-17.

---

## 6. Decisions that are the maintainer's — not taken here

1. **Does the cart own the total?** §1.2 proposes yes, in itemised mode only,
   keeping quick-sale keypad-authoritative. This modifies the 2026-07-27
   arrangement rather than reversing the 2026-07-17 decision, but it is a change
   to a documented decision and is not mine to make.
2. **Catalog or typed lines?** ERPNext `Item` + `Item Price` unlocks item tax
   templates, stock and native metrics, at the cost of master data and of "no
   product catalog in the charge path". Typed free-text lines keep today's speed
   and carry no catalog. §1.5 allows both by making `item_code` optional; which
   one is *default* is a merchant decision.
3. **Do points accrue on the pre-tax subtotal or the grand total?** Today
   `points_for` uses `usd_cents` (`loyalty.py:52`). **§1.2 redefines `usd_cents`
   to be the taxed total, so shipping itemised mode without deciding this
   silently answers it — points would begin accruing on tax, which is the
   minority practice, arrived at by default.** `Crypto Loyalty Award.usd_cents`
   would snapshot the taxed figure too, so the record would not preserve which
   basis was used. Itemised mode is therefore **blocked** on this decision, or
   `points_for` must switch to a pre-tax field as part of it. My recommendation:
   exclude tax.
4. **"Point usage" as asked for is not available.** On a normal register that
   line means points being spent. These points cannot be spent —
   `withdraw: DenyAll`, `Locked`, enrolment blocked — and `harness_loyalty`
   fails if a `Loyalty Point Entry` ever appears. §3 proposes a points-**earned**
   credit line, visually outside the totals column. If what was wanted is points
   reducing the bill, that is a different and much larger conversation about the
   contract, not the UI.
