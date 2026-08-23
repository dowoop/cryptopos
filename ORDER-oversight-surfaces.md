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
cryptopos/cryptopos/report/__init__.py
cryptopos/cryptopos/report/crypto_takings/__init__.py
cryptopos/cryptopos/report/crypto_takings/crypto_takings.json
cryptopos/cryptopos/report/crypto_takings/crypto_takings.py
cryptopos/cryptopos/number_card/settled_not_in_ledger_count/settled_not_in_ledger_count.json
cryptopos/cryptopos/number_card/settled_not_in_ledger_value/settled_not_in_ledger_value.json
cryptopos/cryptopos/dashboard_chart/crypto_takings_30_days/crypto_takings_30_days.json
cryptopos/cryptopos/workspace/cryptopos/cryptopos.json
cryptopos/api.py
cryptopos/harness.py
```

**CORRECTED after the builder's question, which was right.** The first list put
the report at `cryptopos/report/...`, which Frappe never syncs: a Script Report
in module `CryptoPoS` resolves as `cryptopos.cryptopos.report.<name>` and
migrate reads the matching directory. Verified against the running site —
`erpnext/accounts/report/gross_profit/` is the shape, and `number_card/` and
`dashboard_chart/` are sibling module directories with their own records.
A workspace's `number_cards` and `charts` entries are *references*; the
workspace file cannot define them, so shipping only the references would make
the blocks vanish from the desk rather than appear on it.

Delete anything already written under `cryptopos/report/` — leaving a module
Frappe will never load is worse than not having written it.

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

Final ownership checks:

```text
$ git status --short --untracked-files=all
 M ORDER-oversight-surfaces.md
 M cryptopos/api.py
 M cryptopos/cryptopos/workspace/cryptopos/cryptopos.json
 M cryptopos/harness.py
?? cryptopos/cryptopos/dashboard_chart/crypto_takings_30_days/crypto_takings_30_days.json
?? cryptopos/cryptopos/number_card/settled_not_in_ledger_count/settled_not_in_ledger_count.json
?? cryptopos/cryptopos/number_card/settled_not_in_ledger_value/settled_not_in_ledger_value.json
?? cryptopos/cryptopos/report/__init__.py
?? cryptopos/cryptopos/report/crypto_takings/__init__.py
?? cryptopos/cryptopos/report/crypto_takings/crypto_takings.json
?? cryptopos/cryptopos/report/crypto_takings/crypto_takings.py
$ git diff --name-only
ORDER-oversight-surfaces.md
cryptopos/api.py
cryptopos/cryptopos/workspace/cryptopos/cryptopos.json
cryptopos/harness.py
$ git ls-files --others --exclude-standard
cryptopos/cryptopos/dashboard_chart/crypto_takings_30_days/crypto_takings_30_days.json
cryptopos/cryptopos/number_card/settled_not_in_ledger_count/settled_not_in_ledger_count.json
cryptopos/cryptopos/number_card/settled_not_in_ledger_value/settled_not_in_ledger_value.json
cryptopos/cryptopos/report/__init__.py
cryptopos/cryptopos/report/crypto_takings/__init__.py
cryptopos/cryptopos/report/crypto_takings/crypto_takings.json
cryptopos/cryptopos/report/crypto_takings/crypto_takings.py
$ git diff --name-only -- packages/
scope assertion: exactly OWNS plus ORDER result; packages unchanged; obsolete path absent
git diff --check: clean
```

## NOT IN THIS SLICE
- Valuing a crypto position in any currency → D6, rejected four times.
- Changing how booking works → D1 and D2 settled that.
- A profit-and-loss or growth report → ERPNext already reports over Sales
  Invoices, which is the ledger's own question and belongs in its own tools.

---
## RESULT — filled in by the builder, not before

IMPLEMENTATION COMPLETE; DOCKER VALIDATION IS PENDING THE TWO USER-RUN GATES
listed below.

The report, both Number Card records, the Dashboard Chart record, and their
workspace references now live under the corrected `cryptopos/cryptopos/`
module path. The obsolete `cryptopos/report/` tree was deleted. The report
keeps booked and unbooked USD separate, derives both only from `usd_cents`,
keeps native totals as exact text within one rail, and charts booked USD only.
`api.rails()` preserves `binding`, `gap_run`, and `gap_limit`; readiness is
absent by default and is evaluated only for `with_readiness=1`.

Per the environment note, `make lint` was replaced with the repository's local
Ruff binary because `uvx` cannot write its cache here:

```text
$ .venv/bin/ruff check .
All checks passed!
```

The earlier literal `make lint` and `make check` attempts both stopped before
Ruff ran. Their real output was:

```text
uvx ruff@0.16.3 check .
error: Could not acquire lock
  Caused by: Could not create temporary file
  Caused by: Read-only file system (os error 30) at path "~/.cache/uv/.tmpsFuHCh"
make: *** [Makefile:79: lint] Error 2

uvx ruff@0.16.3 check .
error: Could not acquire lock
  Caused by: Could not create temporary file
  Caused by: Read-only file system (os error 30) at path "~/.cache/uv/.tmpnlzfbo"
make: *** [Makefile:79: lint] Error 2
```

The non-Docker portions of `make check` that do not write to unowned paths were
run directly. Core source matrix:

```text
Python 3.9.25
Ran 604 tests in 0.472s
OK (skipped=2)
Python 3.11.15
Ran 604 tests in 0.458s
OK (skipped=2)
Python 3.13.14
Ran 604 tests in 0.498s
OK (skipped=2)
Python 3.14.4
Ran 604 tests in 0.497s
OK
```

The existing, unchanged wheel was installed into four isolated temporary
environments and tested:

```text
Python 3.9 installed-wheel
Ran 604 tests in 0.491s
OK
Python 3.11 installed-wheel
Ran 604 tests in 0.398s
OK
Python 3.13 installed-wheel
Ran 604 tests in 0.395s
OK
Python 3.14 installed-wheel
Ran 604 tests in 0.406s
OK
```

A rebuild was attempted from a temporary copy so nothing under `packages/`
would be modified. It failed because the sandbox could not resolve the build
dependency from PyPI:

```text
error: Failed to build `/tmp/cryptopos-wheel-gate.JfA9qV/core`
  Caused by: Failed to resolve requirements from `build-system.requires`
  Caused by: No solution found when resolving: `hatchling==1.32.0`
  Caused by: Request failed after 3 retries in 10.8s
  Caused by: Failed to fetch: `https://pypi.org/simple/hatchling/`
  Caused by: error sending request for url (https://pypi.org/simple/hatchling/)
  Caused by: client error (Connect)
  Caused by: dns error
  Caused by: failed to lookup address information: Temporary failure in name resolution
```

Proof and terminal gates:

```text
every line of cryptopos_core executes (2250 lines, 604 tests)
every symbol has a row in PROOF.md
methods    29/29
controls   21/21
keys        5/5
every terminal method runs and every control is operated (29 methods, 21 controls)
terminal render: 111 passed, 0 failed
terminal buttons: 129 passed, 0 failed
```

Mutation gates:

```text
every mutation was caught (2084/2099 killed, 15 accepted as equivalent)
terminal.js  132/135  97.8%
every mutation was caught (132/135 killed, 3 accepted as equivalent)
```

The report was also exercised with deterministic booked, unbooked, BTC, and
wei-scale ETH rows, and all exported JSON plus the workspace's encoded content
were parsed and cross-checked:

```text
report aggregation: booked/unbooked separate; native text preserved per rail
report chart: booked USD only
widget JSON: 5 documents parse; workspace references 2 cards, 1 chart, 1 report
git diff --check: clean
```

The first supplemental validator invocation had a bad assertion against the
workspace JSON's nesting. It failed, was corrected, and the corrected run above
passed. The failed Python portion printed:

```text
Traceback (most recent call last):
  File "<stdin>", line 72, in <module>
  File "<stdin>", line 72, in <genexpr>
KeyError: 'links'
```

By explicit instruction, these Docker-only DONE WHEN commands were not run in
this sandbox; the user will run them:

```text
docker exec frappe_docker-backend-1 bash -lc 'cd /home/frappe/frappe-bench && bench --site erp.localhost migrate'
docker exec frappe_docker-backend-1 bash -lc 'cd /home/frappe/frappe-bench/sites && ../env/bin/python -c "
import frappe; frappe.init(site=\"erp.localhost\"); frappe.connect()
from cryptopos import harness; harness.run()"'
```
