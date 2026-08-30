"""Live, read-only shell for the stateless block-identity reorg probe.

    bench --site erp.localhost execute cryptopos.tools.reorg_probe.run

or, from the backend container:

    cd sites && ../env/bin/python ../apps/cryptopos/tools/reorg_probe.py

Add ``--journal PATH`` to the script form to append endpoint observations.  The
journal records; it never supplies an expectation or changes classification or
exit status.  A previous answer from the same endpoint is one node's opinion
(D39), not chain truth, so this deliberately has no rolling header cache and no
common-ancestor walk.

Frappe is imported only inside the live entry points.  Importing this module or
``reorg_probe_core`` opens no socket.  The probe changes no sale, settlement,
invoice, endpoint, or rail configuration; D10 and D39 stand.
"""

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

try:
    from tools import reorg_probe_core as core
except ImportError:
    # Direct execution places tools/, rather than the app root, on sys.path.
    import reorg_probe_core as core

_AGENT = {"User-Agent": "cryptopos-reorg-probe/2.0"}


RETRY_ATTEMPTS = 2
_RETRIED = {"count": 0}


def read_with_retry(attempt, attempts=RETRY_ATTEMPTS, on_retry=None):
    """Call `attempt` until it returns, up to `attempts` times.

    A public endpoint that answers nothing on one read and everything on the
    next is the measured behaviour of the Sepolia and Amoy nodes this probe is
    pointed at, and D38's rule is that "nobody answered" must never read as
    "the chain says it is gone". Retrying serves that rule rather than hiding
    from it: the question is about the chain, and one silent read is not the
    chain's answer.

    What is NOT retried is a refusal. `core.NotFound` is a real answer -- an
    esplora 404 -- and asking again would only turn an answer into noise.

    Every retry that was needed is counted and reported beside the result, so
    an endpoint degrading stays visible instead of being smoothed away. A
    number nobody prints is a number nobody acts on.
    """
    last = None
    for number in range(max(1, int(attempts))):
        try:
            return attempt()
        except core.NotFound:
            raise
        except Exception as failure:
            last = failure
            if number + 1 < attempts and on_retry is not None:
                on_retry()
    raise last


def classify_with_retry(classify, is_unreachable, on_retry=None,
                        attempts=RETRY_ATTEMPTS):
    """Classify, and if nobody answered, ask the chain once more.

    The transport retry above catches a socket that failed. It does NOT catch
    the failure this probe actually suffers: a JSON-RPC endpoint that returns
    `{"result": null}`. That is a successful HTTP read, so the transport never
    sees it -- the core turns it into UNREACHABLE further up. Measured on this
    deployment, one unlucky read put 5 to 12 booked sales in UNREACHABLE on
    runs where nothing on any chain had changed, and a single repeat cleared
    every one of them.

    So the repeat belongs here, around the whole classification, which is where
    the silence becomes a verdict. It is bounded, and it never converts a
    verdict into a better one: GONE, REMINED and SHALLOW are answers and are
    returned the first time they are given.
    """
    result = classify()
    for _ in range(max(1, int(attempts)) - 1):
        if not is_unreachable(result):
            return result
        if on_retry is not None:
            on_retry()
        result = classify()
    return result


def http_transport(endpoint, kind, target, params):
    """Make one live read for the pure core; no caller imports Frappe here."""
    if kind == "rpc":
        body = json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": target, "params": params}
        ).encode()
        request = urllib.request.Request(
            endpoint,
            data=body,
            headers={**_AGENT, "Content-Type": "application/json"},
        )
    elif kind in ("json", "text"):
        request = urllib.request.Request(
            "{}{}".format(endpoint.rstrip("/"), target), headers=_AGENT
        )
    else:
        raise ValueError("unknown transport kind {!r}".format(kind))

    def once():
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                if kind == "text":
                    return response.read().decode("utf-8").strip()
                return json.load(response)
        except urllib.error.HTTPError as failure:
            if failure.code == 404:
                raise core.NotFound(str(failure))
            raise

    def counted():
        _RETRIED["count"] += 1

    return read_with_retry(once, on_retry=counted)


def _field(record, name, default=None):
    if isinstance(record, dict):
        return record.get(name, default)
    return getattr(record, name, default)


def _partition_sales(rows):
    """Return booked population, excluded tx-bearing sales, and invisible ids."""
    booked = []
    unbooked_with_tx = []
    booked_without_tx = []
    for row in rows:
        sale = dict(row)
        if sale.get("sales_invoice"):
            booked.append(sale)
            if not (sale.get("tx_id") or "").strip():
                booked_without_tx.append(sale)
        elif (sale.get("tx_id") or "").strip():
            unbooked_with_tx.append(sale)
    return booked, unbooked_with_tx, booked_without_tx


def _probe_inputs(rail, sale, explicit_thresholds):
    """Ask the Crypto Rail DocType for this sale's endpoint and gate."""
    mode = sale.get("mode")
    rail_name = _field(rail, "name") or sale.get("rail_key")
    try:
        endpoint = rail.endpoint_for(mode)
    except Exception as failure:
        return "", None, "Crypto Rail endpoint_for({!r}) failed: {}".format(
            mode, failure
        )
    if not endpoint:
        return "", None, "Crypto Rail endpoint_for({!r}) returned no endpoint".format(
            mode
        )
    try:
        gate = rail.gate_for(mode)
    except Exception as failure:
        return endpoint, None, "Crypto Rail gate_for({!r}) failed: {}".format(
            mode, failure
        )
    if isinstance(gate, bool) or not isinstance(gate, int):
        return endpoint, None, "Crypto Rail gate_for({!r}) returned unusable {!r}".format(
            mode, gate
        )
    if gate > 0:
        return (
            endpoint,
            core.depth_maturity(
                gate, "Crypto Rail gate_for({!r})".format(mode)
            ),
            None,
        )

    override = explicit_thresholds.get(rail_name)
    if override == "finalized":
        maturity = core.finalized_maturity(
            "explicit --maturity {}=finalized overrode Crypto Rail "
            "gate_for({!r})={}".format(rail_name, mode, gate)
        )
    elif isinstance(override, int) and not isinstance(override, bool) and override > 0:
        maturity = core.depth_maturity(
            override,
            "explicit --maturity {}={} overrode Crypto Rail gate_for({!r})={}"
            .format(rail_name, override, mode, gate),
        )
    else:
        return (
            endpoint,
            None,
            "Crypto Rail gate_for({!r}) returned {}; refusing without an "
            "explicit --maturity override".format(mode, gate),
        )
    return endpoint, maturity, None


def _journal_history(path):
    history = {}
    journal = Path(path)
    if not journal.exists():
        return history
    with journal.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except (TypeError, ValueError):
                continue
            endpoint = row.get("endpoint")
            tx_id = row.get("tx_id")
            block_hash = row.get("block_hash")
            if endpoint and tx_id and block_hash:
                history.setdefault((endpoint, tx_id), []).append(row)
    return history


def _same_block_hash(left, right):
    """Compare hex identities case-insensitively and base58 identities exactly."""
    hexadecimal = set("0123456789abcdefABCDEF")
    left_hex = isinstance(left, str) and (
        left.startswith("0x") or (len(left) == 64 and set(left) <= hexadecimal)
    )
    right_hex = isinstance(right, str) and (
        right.startswith("0x") or (len(right) == 64 and set(right) <= hexadecimal)
    )
    if left_hex and right_hex:
        return left.lower() == right.lower()
    return left == right


def _record_journal(path, observations, output):
    """Append observations and print history differences without judging them."""
    if not path:
        return
    try:
        history = _journal_history(path)
        # datetime.UTC exists only from Python 3.11; this tool supports 3.9.
        timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")  # noqa: UP017
        rows = []
        for sale in observations:
            for transaction in sale.transactions:
                row = {
                    "timestamp": timestamp,
                    "endpoint": sale.endpoint,
                    "sale": sale.sale,
                    "tx_id": transaction.tx_id,
                    "block_height_or_slot": transaction.block_position,
                    "block_hash": transaction.block_hash,
                    "state": transaction.state,
                }
                rows.append(row)
                if transaction.block_hash:
                    prior = history.get((sale.endpoint, transaction.tx_id), [])
                    different = next(
                        (
                            old
                            for old in reversed(prior)
                            if old.get("block_hash")
                            and not _same_block_hash(
                                old.get("block_hash"), transaction.block_hash
                            )
                        ),
                        None,
                    )
                    if different is not None:
                        print(
                            "  RE-MINED (journal evidence) {}: tx {} — "
                            "observation-vs-observation at the same endpoint; "
                            "earlier block hash {}, current {}; live state remains {}"
                            .format(
                                sale.sale,
                                transaction.tx_id,
                                different["block_hash"],
                                transaction.block_hash,
                                transaction.state,
                            ),
                            file=output,
                        )
        with Path(path).open("a", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, sort_keys=True) + "\n")
    except Exception as failure:
        print(
            "  JOURNAL UNREACHABLE: could not read or append {!r}: {}; "
            "live states and exit status are unchanged".format(path, failure),
            file=output,
        )


def _report_outcome(observations, output):
    """Print a truthful conclusion and return the three-way process result."""
    actionable = core.actionable_count(observations)
    unreachable = sum(
        observation.state == core.UNREACHABLE for observation in observations
    )
    result = core.result_code(observations)
    if actionable:
        print(
            "  {} actionable sale(s) are not backed; this read-only probe changes "
            "nothing (D10).".format(actionable),
            file=output,
        )
    if unreachable:
        print(
            "  INCONCLUSIVE: {} booked sale(s) are UNREACHABLE. They are absent "
            "from the actionable count, remain distinct from GONE (D38), and "
            "prevent a green result.".format(unreachable),
            file=output,
        )
    elif not actionable:
        print(
            "  PASS: every booked sale was answered and every settled transaction "
            "is BACKED",
            file=output,
        )
    return result


def _transaction_summary(observation):
    return ", ".join(
        "{} tx {}".format(transaction.state, transaction.tx_id)
        for transaction in observation.transactions
    )


def run(journal=None, maturity_thresholds=None, transport=http_transport, output=None):
    """Read booked sales; return 1 actionable, 2 inconclusive, or 0 proven."""
    import frappe

    _RETRIED["count"] = 0

    if output is None:
        output = sys.stdout
    explicit_thresholds = maturity_thresholds or {}
    rows = frappe.get_all(
        "Crypto Sale",
        fields=[
            "name",
            "state",
            "end_kind",
            "tx_id",
            "watch_scratch",
            "rail_key",
            "mode",
            "sales_invoice",
            "credited_native",
        ],
        order_by="creation desc",
    )
    sales, unbooked_with_tx, booked_without_tx = _partition_sales(rows)
    print(
        "  population: {} booked sale(s); {} tx-bearing unbooked sale(s) "
        "excluded; {} booked sale(s) have no headline tx_id".format(
            len(sales), len(unbooked_with_tx), len(booked_without_tx)
        ),
        file=output,
    )
    for sale in unbooked_with_tx:
        print(
            "  EXCLUDED UNBOOKED {}: tx {}".format(sale["name"], sale["tx_id"]),
            file=output,
        )
    for sale in booked_without_tx:
        print(
            "  UNREACHABLE BOOKED-NO-TX {}: invoice {} has no headline tx_id"
            .format(sale["name"], sale.get("sales_invoice")),
            file=output,
        )
    if not sales:
        print("  no booked sale exists; the empty population is answered", file=output)
        return 0

    rails = {}
    for rail_name in {sale.get("rail_key") for sale in sales if sale.get("rail_key")}:
        try:
            rails[rail_name] = frappe.get_doc("Crypto Rail", rail_name)
        except Exception:
            rails[rail_name] = None

    observations = []
    for sale in sales:
        rail = rails.get(sale.get("rail_key"))
        if rail is None:
            observation = core.unreachable_sale(
                sale,
                "",
                "Crypto Rail {!r} could not be read".format(sale.get("rail_key")),
            )
        else:
            endpoint, maturity, refusal = _probe_inputs(
                rail, sale, explicit_thresholds
            )
            if refusal is not None:
                observation = core.unreachable_sale(sale, endpoint, refusal)
            else:
                observation = classify_with_retry(
                    lambda: core.classify_sale(
                        sale,
                        {"family": _field(rail, "family"), "endpoint": endpoint},
                        maturity,
                        transport,
                    ),
                    lambda answer: answer.state == core.UNREACHABLE,
                    on_retry=lambda: _RETRIED.__setitem__(
                        "count", _RETRIED["count"] + 1
                    ),
                )
        observations.append(observation)
        invoice = sale.get("sales_invoice") or "(not booked)"
        print(
            "  {} {}: {} — {}; state={} end={} invoice={}".format(
                observation.state,
                observation.sale,
                _transaction_summary(observation),
                observation.reason,
                sale.get("state"),
                sale.get("end_kind"),
                invoice,
            ),
            file=output,
        )

    _record_journal(journal, observations, output)

    counts = {
        state: sum(observation.state == state for observation in observations)
        for state in (
            core.BACKED,
            core.SHALLOW,
            core.REMINED,
            core.GONE,
            core.UNREACHABLE,
        )
    }
    print(file=output)
    print(
        "  checked {} sale(s): {} BACKED, {} SHALLOW, {} REMINED, {} GONE, "
        "{} UNREACHABLE".format(
            len(observations),
            counts[core.BACKED],
            counts[core.SHALLOW],
            counts[core.REMINED],
            counts[core.GONE],
            counts[core.UNREACHABLE],
        ),
        file=output,
    )
    print(
        "  sale precedence: GONE > REMINED > SHALLOW > UNREACHABLE > BACKED; "
        "every settled transaction must be BACKED for the sale to be BACKED",
        file=output,
    )
    if _RETRIED["count"]:
        print(
            "  endpoint health: {} read(s) needed a second attempt. The "
            "verdicts above stand; this counts the endpoint, not the "
            "chain.".format(_RETRIED["count"]),
            file=output,
        )
    return _report_outcome(observations, output)


def _maturity_arguments(values):
    thresholds = {}
    for value in values:
        rail, separator, count = value.partition("=")
        if not separator or not rail:
            raise ValueError(
                "--maturity must be RAIL=POSITIVE_CONFIRMATIONS or "
                "RAIL=finalized, got {!r}".format(value)
            )
        if count == "finalized":
            thresholds[rail] = count
            continue
        try:
            threshold = int(count)
        except ValueError:
            threshold = 0
        if threshold < 1:
            raise ValueError(
                "--maturity must be RAIL=POSITIVE_CONFIRMATIONS or "
                "RAIL=finalized, got {!r}".format(value)
            )
        thresholds[rail] = threshold
    return thresholds


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", help="append observation JSONL; off by default")
    parser.add_argument(
        "--maturity",
        action="append",
        default=[],
        metavar="RAIL=COUNT|finalized",
        help="explicit override only when Crypto Rail gate_for(mode) is unusable",
    )
    arguments = parser.parse_args(argv)
    try:
        thresholds = _maturity_arguments(arguments.maturity)
    except ValueError as failure:
        parser.error(str(failure))

    import frappe

    frappe.init(site="erp.localhost")
    frappe.connect()
    try:
        return run(arguments.journal, thresholds)
    finally:
        frappe.destroy()


if __name__ == "__main__":
    raise SystemExit(main())
