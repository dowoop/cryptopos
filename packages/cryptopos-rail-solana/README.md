# cryptopos-rail-solana

A Solana devnet SOL payment rail for CryptoPoS, shipped as a **separate
distribution**. Installing it is what adds the rail; nothing in the terminal is
edited.

It is the first rail this project ever gained that way, and it was proved by
taking a real payment: `CPS-2026-00328`, 102,000 lamports, booked into ERPNext
as `ACC-SINV-2026-00075` on 2026-08-25.

## What it does that the other rails cannot

**It binds a payment to a sale.** Every payment request carries a `reference`
account derived as `base58(sha256(intent_id))`, the payer includes it on the
transfer, and observation asks the chain for signatures that touch *that* —
never for transfers that happened to arrive at the merchant address for the
right amount.

**The reference is checked on the instruction, not in the transaction, and the
difference is the whole property.** `getSignaturesForAddress` returns every
transaction whose account list merely *mentions* the reference, and Solana Pay
permits several references on one transfer. So the rail decodes the
`SystemInstruction::Transfer` instructions, credits only those whose recipient
is this merchant and whose account list carries this sale's reference and no
other extra account, and takes the amount from the instruction's own u64 rather
than from the recipient's balance delta.

The first version of this package did none of that: it searched the account list
and credited the balance delta. One 100-lamport transfer naming two sales'
references settled a 60-lamport sale *and* a 40-lamport sale. See cryptopos
`DECISIONS.md` **D33**.

So two concurrent sales to one merchant address are told apart with no
ambiguity. cryptopos `DECISIONS.md` **D5** — a shared address cannot be made
safe by bookkeeping — describes the three EVM rails and does not describe this
one. Only `btc` (D7) was in that position before, and it pays for it with block
time; Solana finality is a commitment level, not a depth, so this rail does not.

The reference is **derived, never stored**: `observe` recomputes it from the
intent id. A reference that differed between the request and the watch would be
a sale that could never be seen to have been paid.

## Installing it into a Frappe deployment

**Install it into every container that runs app code, not just one.** In the
`frappe_docker` stack, `backend`, `queue-short`, `queue-long` and `scheduler`
have **separate Python environments**. `charge()` runs in one and the watch
heartbeat runs in the others, so installing into `backend` alone gives you a
terminal that can *sell* on a rail it cannot *watch* — which is worse than not
having the rail. That failure was measured here with real money on the first
attempt; the sale ended `needs_review` with the payment already broadcast.

```bash
for c in backend queue-short queue-long scheduler; do
  docker compose -p frappe_docker exec -T $c bash -lc \
    'cd /home/frappe/frappe-bench && env/bin/python -m pip install ./apps/cryptopos/packages/cryptopos-rail-solana'
done
docker compose -p frappe_docker restart backend scheduler queue-long queue-short
```

Then confirm every process agrees before charging anything:

```bash
docker compose -p frappe_docker exec -T backend bash -lc \
  'cd sites && ../env/bin/python ../apps/cryptopos/tools/rails_probe.py'
```

Finally create a `Crypto Rail` row with `catalog_key = solana:devnet/native:sol`,
`native_decimals = 9`, `unit_name = lamport`, an endpoint, and a recipient.

## Ceilings

- **Devnet only.** `readiness` asks the endpoint for its genesis hash and
  refuses anything that is not devnet. There is no mainnet mode here.
- **Native SOL only.** This rail names no token mint, so credit comes from the
  lamport balance delta and it never reads token balances. An SPL rail is a
  different rail with a different key; the two must not share code paths,
  because a token transfer moves no lamports at the owner and would credit zero
  against a finalized signature.
- **A transfer it cannot attribute is not attributed at all.** An instruction
  carrying two references, or an amount the recipient's balance cannot account
  for, is reported unreadable and the sale goes to review. There is real money
  involved and no way to tell whose it is; a person decides.
- **An empty answer from a pruned node is not "nobody paid".** The rail asks
  `minimumLedgerSlot` and refuses to read silence as an answer when the node has
  thrown away the slots this sale cares about.
- **A history it could not read in full does not settle.** The signature walk
  stops at a safety limit, and a credit the rail knows is a lower bound must not
  become a permanent record. Spamming the reference can therefore force a sale
  into review — a denial of service, not a theft.
- **It does not decode address lookup tables.** A v0 transaction can load
  accounts from a lookup table; those arrive in `meta.loadedAddresses` and not
  in `accountKeys`. Rather than guess, the rail reports the amount as unreadable
  and retries. Saying "I do not know" is the only answer here that cannot
  produce a part-paid decision about a customer who paid in full.
- **`finalized` settles, `confirmed` never does.** `processed` is unreachable on
  devnet — it answers `-32602 "Method does not support commitment below
  'confirmed'"` — so no branch pretends otherwise.

## Zero dependencies

Standard library only, plus `cryptopos_core` for the plugin contract. A payment
terminal that pulls a transitive dependency tree is a payment terminal whose
supply chain you no longer know.

## Its own tests

```bash
PYTHONPATH=../cryptopos-core/src:src python3 -m unittest discover -s tests -t .
```

Fourteen checks, all offline, the node behind one seam. **They are not
sufficient and the package says so.** The first version of `DEVNET_GENESIS_HASH`
was the real hash truncated at 32 of its 44 characters — the rail would have
refused every real devnet node as not-devnet — and all fourteen passed, because
none of them touches a node.

So the facts are checked separately, against the chain:

```bash
PYTHONPATH=../cryptopos-core/src python3 live_check.py     # 10 checks, read-only
```

It asserts the genesis hash the node actually answers, that all four
capabilities are ready through a real endpoint, that a derived reference is an
address the node accepts and has no history, that `getSignaturesForAddress` and
`getTransaction` both still refuse `processed` — the measurement the missing
branch rests on, rather than the protocol's reputation — and that the transfer
this rail was proved with is still on the chain and still moved 102,000
lamports.

The offline suite proves the logic; this proves the facts. Neither is
sufficient alone, and the truncated hash is why.
