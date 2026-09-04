# Working on this repo

Two halves live here, and they have very different requirements.

| | `packages/cryptopos-core/` | `cryptopos/` |
|---|---|---|
| What it is | a plain Python package | a Frappe app |
| Needs | Python, nothing else | bench + MariaDB + Redis + Node |
| Runs on this machine | **yes** | no — it needs the Docker stack |
| Test loop | under a second | minutes, and a container |
| Distributed as | a wheel on PyPI | an app baked into an image |

Everything below is about the first column. That is deliberate: the package is
the half that can be developed with full focus on one machine, and keeping it
runnable without a framework is not a convenience — it is the property that
makes it worth publishing at all.

## Setup

```bash
make dev
```

Creates `.venv` (Python 3.14), installs `cryptopos_core` editable, and pins
`ruff`. Nothing else is required — the package has no dependencies and no
test-runner to install. VS Code and Cursor pick the interpreter up from
`.vscode/settings.json` automatically.

## The loop

```bash
make test      # the core suite, well under a second
make watch     # the same, re-run whenever a file changes
make terminal  # the two terminal suites (node, no browser)
make prove     # nothing unexecuted, nothing unclicked, nothing unexplained
make worth     # break it on purpose; fail if the suites do not notice
make lint      # ruff over the whole repo
make fmt       # organise imports, apply fixes, format
```

`make test` is the one you live in. It runs 579 tests with no network, no
database and no framework, which is why it is fast enough to run on every
save.

`make prove` is the one to run before you believe yourself. It is three gates:

| gate | fails when |
|---|---|
| `tools/prove.py` | a line of `cryptopos_core` never executes |
| `tools/prove.py` | a symbol has no row in [PROOF.md](PROOF.md) |
| `tools/prove_terminal.js` | a terminal method is never called, or a control never clicked |

All three read their inventory from the source, so adding a function or a
button puts it on the required list immediately — you cannot forget to
register it, only fail until you do.

`make worth` is the one that tells you whether any of that meant anything. It
rewrites one operator or constant at a time and runs the suites against the
rewritten copy; a mutant that SURVIVES is code you made wrong while every test
still passed. It takes about half a minute for the core and three seconds for
the terminal, both parallel across your cores.

When something survives you have two honest options and no third:

1. **Strengthen the assertion.** Usually the mutation is telling you a
   boundary is only tested from one side, or that a fixture is built from the
   constant it was meant to pin.
2. **Record it as equivalent.** Add it to `EQUIVALENT` in the tool with the
   reason it cannot change observable behaviour — and check that reason rather
   than assuming it. Every entry in there was verified, several of them over
   thousands of random inputs.

Deleting the test, loosening the assertion, or adding it to `EQUIVALENT` with
a vague reason all defeat the point of running it.

The terminal gate works by re-running both node suites with `CPOS_COVERAGE`
set; `tests/terminal_harness.js` then wraps the page class and records every
method call, dispatched control and key pressed. Normal runs are unaffected.

## Before you push

```bash
make check
```

Runs the lot: lint, the suite on **3.9, 3.11, 3.13 and 3.14** from source, then
builds the wheel and sdist and runs the suite again against the *installed*
wheel on each of those versions — then `make prove`, `make terminal` and
`make worth` on top, so a green `check` also means nothing is unexecuted,
unclicked or unexplained, and that breaking any of it on purpose is caught.

That last step is not redundant. `requires-python = ">=3.9"` is a promise to
anyone who installs this, and the packaging assertions — no non-stdlib
imports, no declared dependencies, no `frappe` anywhere in the source — only
run when there is real installed metadata to read. From a source tree they
skip. The wheel run is where they actually mean something.

## The Frappe half

`cryptopos/` cannot be imported here: `import frappe` will fail, and the
editor is configured not to report that as an error, because it is expected
rather than wrong. Those three modules are thin adapters — they catch the
core's exceptions and call `frappe.throw`, or read settings and construct an
`OotleReader`. There is deliberately nothing else in them.

The one part of that half which **can** be exercised here is the terminal
page, because it is JavaScript and needs no bench. `make terminal` runs it
against a stubbed desk. Note what the stub does and does not do: it parses the
HTML the page actually rendered, registers the handlers `wire()` attaches, and
dispatches real clicks, changes and keypresses at them — so a disconnected
button fails. It has no layout, no CSS, no focus and no bubbling, so nothing
about *appearance* is proved by it.

To exercise that half you need the Docker stack:

```bash
make docker-check
```

If it reports the socket is unreachable, this shell is not in the `docker`
group:

```bash
sudo usermod -aG docker $USER   # then log out and back in
```

## Where the chains actually are

This matters for planning package work, so it is written down rather than
rediscovered.

The rail table defines **12 rails across 8 chain families**, and every one of
them is testnet:

| maturity | count | rails |
|---|---|---|
| `works` | 6 | `eth`, `usdc-eth` (Sepolia), `pol`, `usdc-pol` (Amoy), `sol`, `usdc-sol` (devnet) |
| `partial` | 3 | `btc` (testnet4), `xtr` (esmeralda), `dash` (testnet) |
| `sim-always` | 3 | `xmr` (stagenet), `xtm` (esmeralda), `zec` (testnet) |

**All 12 now live in `cryptopos_core.rails`**, carried across whole with their
maturity notes, and `cryptopos_core.uri` builds the payment URI for 11 of them
(`xtr` has no branch — see below). Both are testable in under a second with no
bench, which was the entire argument for moving them.

What did **not** come across, and the reason is the same for all of it — none
of it can be confirmed without the host it reads:

| stayed behind | why |
|---|---|
| `chargeable_in_mode()` and its endpoint ladders | reads operator config through `store.load_endpoints()` |
| measured finality / watchability tables | ~500 lines of interdependent measured timing state |
| `Crypto Rail` doctype + `install.py` seeds | needs a running bench to exercise at all |
| the app's own charge path | see the warning below |

So a rail's *identity* is now in one place. What remains split is a rail's
*deployment* — which endpoint an operator chose, and whether the rail is
switched on — and that split is deliberate. Those are exactly what Frappe is
good at.

### Two things to know before using the new modules

**Price sales with `rails.invoice_amount`.** Two functions in the package
convert cents to native units and they do not agree: the rail path rounds once
at *display* precision and scales up, `rates.native_for` divides straight to
native precision. That difference used to be a footnote; it is now known to be
a defect in one direction. A decimal-amount URI carries the display form, so
an amount produced by `native_for` on SOL or XMR yields a QR that asks for
*less than the sale invoiced* — the customer pays what they were shown and the
sale sits short of itself forever.

`invoice_amount` cannot produce such an amount, and `build_uri` now raises
`AmountNotRepresentable` rather than emit one. `native_for` is unchanged and
is documented as the primitive it always was.

**The app still builds its own Bitcoin URI.** `cryptopos/charge.py` keeps
`_bitcoin_uri`, which emits a `Decimal.normalize()` amount (`0.001953`) where
`uri.build_uri` emits a padded one (`0.00195300`). Both are valid BIP-21 and
they are different strings. Rewiring the app to the package would change what
a customer scans, so it needs `cryptopos.harness.run` on a live bench to
confirm — which is why it was not done here.

`mainnet` is refused by decision, not by omission. See `charge.py` and
`DESIGN_REGISTER.md` before changing that.
