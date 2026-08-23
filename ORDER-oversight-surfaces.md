# ORDER — the operator can see the money without leaving the desk

STATUS: OPEN — do not start until ORDER-per-sale-addresses.md has landed.
OWNER: one builder. Nothing else may edit these paths while it is open.

## WHY

Everything this terminal knows about its own takings is currently reachable
only by reading `Crypto Sale` rows one at a time. `api.unbooked` was added when
D1 found that a settled sale could fail to book and never be retried — it
answers the single most important question and it answers it to a JSON caller,
not to the operator standing at the desk. The workspace has four shortcuts and
no charts and no number cards.

The questions an operator actually has are: what did I take today, on which
rail, how much has reached the ledger, and what has not.

## OWNS
```
cryptopos/report/__init__.py
cryptopos/report/crypto_takings/__init__.py
cryptopos/report/crypto_takings/crypto_takings.json
cryptopos/report/crypto_takings/crypto_takings.py
cryptopos/cryptopos/workspace/cryptopos/cryptopos.json
cryptopos/api.py
cryptopos/harness.py
```

## READS — may be opened, must be left byte-identical
```
cryptopos/settle.py
cryptopos/catalog.py
cryptopos/cryptopos/doctype/crypto_sale/crypto_sale.json
cryptopos/cryptopos/doctype/crypto_rail/crypto_rail.json
DECISIONS.md
```

## WHAT TO BUILD

### 1. A Query Report, `Crypto Takings`

Script report, `ref_doctype` `Crypto Sale`, filtered by a date range and
optionally a rail. One row per rail per day, with these columns and no others:

| column | what it is |
|---|---|
| date, rail | the grouping |
| sales | how many sales ended in `confirmed` |
| booked_usd | sum of `usd_cents` for sales that carry a `sales_invoice`, shown as currency |
| unbooked_usd | sum of `usd_cents` for `confirmed` sales that carry none |
| credited_native | total native units credited on that rail that day, as **text** |
| unit | the rail's `unit_name` |

**Three rules about those columns, and each has already cost this project
something:**

- **`credited_native` is text, and it is never summed across rails.** Adding
  wei to satoshi produces a number that means nothing. Group by rail, always.
  It is text because an 18-decimal total exceeds 2^53 and the desk renders
  through JavaScript — see D4, and `terminal.js` which already reads natives
  with `BigInt`.
- **Never convert a native amount to a currency figure.** D6 records why in
  full: an `Asset` code carries no network, this terminal refuses mainnet, and
  every payment it can book is therefore a test token. A column valuing test
  ETH at the mainnet rate would be exactly the false-but-authoritative number
  that decision exists to prevent. The USD columns come from `usd_cents`, which
  is what the cashier charged and what the ledger booked.
- **`booked_usd` and `unbooked_usd` are separate columns, never one total.**
  The gap between them is the number D1 exists to keep visible.

### 2. Number cards and a chart on the workspace

Add to `cryptopos/cryptopos/workspace/cryptopos/cryptopos.json`:

- a number card for **unbooked settled sales** (count), and one for their
  **value** in USD;
- a chart of booked takings over the last 30 days.

The unbooked cards are the ones that matter. A zero there is the healthy
reading, so make the label say what a non-zero means — "settled, not yet in
the ledger" rather than "unbooked", which reads like a category rather than a
problem.

### 3. Rail health, asked for rather than polled

Extend `api.rails()` with the readiness of each rail — what its adapter can
actually do through the endpoint this deployment configured, via
`catalog.readiness_for`.

**This makes a network call per rail, so it must not be on the default path.**
`api.rails()` is called every time the terminal page loads. Add the readiness
under an explicit opt-in argument (`with_readiness=0` by default) and give the
desk a way to ask for it deliberately. A terminal that hangs at the counter
because a public RPC is slow is worse than one that does not show rail health.

## PROOF — harness checks

Add to `cryptopos/harness.py`, and make each one assert rather than print:

- the report runs and returns columns and rows without raising;
- a settled booked sale appears in `booked_usd` and not in `unbooked_usd`;
- a settled unbooked sale appears in `unbooked_usd` and not in `booked_usd`;
- `credited_native` is text and rails are never combined into one row;
- `api.rails()` makes no network call by default — assert the readiness key is
  absent unless asked for;
- `api.rails(with_readiness=1)` returns a readiness for every enabled rail.

The harness already deletes the sales and invoices it creates; anything you add
must go through `_charge` so it is cleaned up too.

## INVARIANTS

- No new dependency.
- Nothing under `packages/` changes.
- `make lint` clean, `make check` green.
- The live harness passes with no failures.
- No column, card or chart converts a native amount into a currency.

## DONE WHEN
```bash
make lint
make check
docker exec frappe_docker-backend-1 bash -lc 'cd /home/frappe/frappe-bench && bench --site erp.localhost migrate'
docker exec frappe_docker-backend-1 bash -lc 'cd /home/frappe/frappe-bench/sites && ../env/bin/python -c "
import frappe; frappe.init(site=\"erp.localhost\"); frappe.connect()
from cryptopos import harness; harness.run()"'
```

## NOT IN THIS SLICE
- Valuing a crypto position in any currency → D6, rejected four times.
- Changing how booking works → D1 and D2 settled that.
- A profit-and-loss or growth report → ERPNext already reports over Sales
  Invoices, which is the ledger's own question and belongs in its own tools.

---
## RESULT — filled in by the builder, not before
