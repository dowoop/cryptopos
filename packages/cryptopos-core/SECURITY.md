# Security

This package builds the string a customer scans to send money. Getting that
string wrong is not recoverable, so this document says plainly what is
verified, what is not, and where the boundary sits.

## What this package guarantees

**Addresses are checksum-verified, not pattern-matched.** A regex accepts a
single-character typo; a checksum does not, and a typo is the realistic
failure — an operator pasting a receiving address with one character lost to a
mis-selected copy.

| rail | check | network bound? |
|---|---|---|
| `btc` | Bech32 / Bech32m (BIP-173, BIP-350), Base58Check | yes — `bc`/`tb`, version bytes |
| `dash` | Base58Check, Dash version bytes | yes |
| `zec` | Base58Check, 2-byte transparent version | yes |
| `xmr` | Monero base58 + Keccak-256 checksum + prefix-specific exact length | yes — prefix 18/19/42 vs 53/54/63 |
| `eth` `pol` `usdc-*` | EIP-55, when the address carries one | **no** — EVM addresses encode no network |
| `sol` `usdc-sol` | length only (32-byte key) | **no** — Solana addresses carry no checksum |
| `xtm` `xtr` | none | no |

Rails in the last three rows return `unchecked`, never `ok`. **`build_uri`
refuses `unchecked` on mainnet.**

**Amounts in a URI state the invoice exactly.** A decimal-amount scheme that
truncated would ask the customer for less than the sale expects, producing a
sale that can never settle. Refused rather than emitted.

**Sale-binding fields are structural, not arbitrary query text.** Solana
references must decode to 32-byte public keys. Zcash transparent payments use
a fresh address as their binding and never carry a memo: ZIP-321 requires a
wallet to reject a memo associated with a transparent recipient.

**Mode names are closed.** Only `demo`, `testnet`, and `mainnet` exist. An
unknown value is refused before pricing or URI construction, so a typo cannot
downgrade real-money policy.

**Real money is priced under stricter rules**: never from the demo constant,
never from one uncorroborated feed, never when feeds disagree beyond 2%.

**Policy and price-feed reads are https-only**, including across redirects.
Responses are read under explicit size limits, and policy indexer URLs cannot
contain credentials, queries, or fragments. The development-only
`allow_insecure` option permits HTTP—not local files or arbitrary URL schemes.
The built-in Bitcoin and EVM transports also disable proxies autodetected from
the process environment or operating system. A deployment that intentionally
needs a proxy must provide an explicit reviewed transport instead of inheriting
ambient routing.

**Complete plugins verify the network provider, not its hostname.** Bitcoin
Testnet 4 pins the BIP-94 genesis hash. Sepolia and Amoy verify `eth_chainId`.
Ootle observation requires the indexer to identify itself as Esmeralda and
return a valid epoch. Baselines are captured before payer instructions are
created and are bound to the recipient. Observation batches are bound to the
intent, recipient, baseline, and provider before settlement can consume them.
The same endpoint must perform later observations.

**Settlement is rail-specific.** Bitcoin testnet requires a mined transfer;
Sepolia requires a successful receipt and three confirmations; Polygon waits
for the transaction's block to reach the `finalized` tag. Mempool sightings,
failed receipts, removed logs, old address history, and already-claimed
transactions do not book income. Every contributing transaction ID is returned
for an atomic host claim; a mature payment timestamped after the host-supplied
expiry enters review instead of settlement.

## What this package does NOT guarantee

* **That an address is yours.** Every check here is about form and network. An
  address can be perfectly valid and belong to someone else. Verifying that
  the operator controls the key is the host's job and cannot be done here.
* **That every catalog rail is safe to charge on.** Only
  `readiness.chargeable` makes that runtime claim for a concrete plugin and
  configuration. A catalog entry or payment URI alone does not.
* **A compromised rate feed that answers plausibly.** HTTPS and downgrade
  refusal protect the path, while corroboration and spread rules limit a
  single bad source. They cannot prove that two independent vendors were not
  compromised or wrong in the same direction.
* **Ownership of `xtm` / `xtr` addresses.** The Ootle payment adapter accepts
  only the supported account/component identifier shape, but it has no local
  checksum or proof of key control. Minotari remains unchecked.
* **That the policy tier read is mainnet.** The default indexer is testnet and
  there is no published mainnet one. `promise()` returns the indexer that
  answered; display it.
* **Per-sale binding for Ootle's shared account.** Esmeralda's filtered vault
  event stream attributes each final deposit to a transaction ID and exact
  amount, so the claimed-transaction set prevents double credit. It does not
  give the payment a sale reference or unique recipient: attribution still
  relies on the static account plus exact amount inside the lock window.
* **The payer wallet's Bitcoin or Solana network selection.** BIP-21 does not
  encode Testnet 4, and Solana Pay does not encode a cluster. The observer is
  network-verified; the host must still tell the payer which wallet network to
  use.
* **That a correctly identified provider is honest.** A genesis hash or chain
  ID prevents wrong-network configuration; it does not make a third-party RPC
  or indexer trustless. Production deployments should use an operator-owned
  node/indexer or a separately reviewed corroborating provider plugin. Public
  endpoints also learn which recipient addresses are being watched.
* **Completeness of a provider's history answer.** Receipt, block-hash,
  contract, and topic checks validate each EVM transfer the provider returns;
  ordinary `eth_getLogs` does not prove that the provider returned every
  matching log. Likewise, an Esplora endpoint can omit address history. This
  can suppress or delay a real payment even when it cannot manufacture a
  trustworthy one without controlling all provider facts.
* **Ethereum finality on the Sepolia rails.** Their three-confirmation rule is
  an explicit test-flow policy, not Ethereum's `safe` or `finalized` consensus
  tag. It must not be copied as the acceptance rule for a valuable mainnet
  rail. Amoy USDC separately requires the Polygon `finalized` tag.
* **Sandboxing third-party plugins.** Entry-point discovery imports and runs
  installed Python code before structural conformance can inspect it. Install
  rail packages only from sources trusted to execute inside the host process;
  the registry is a contract checker, not a code sandbox.
* **The invoice clock and late-payment handling.** The host chooses and persists
  the expiry. Complete rails enforce that supplied boundary and return
  `needs-review` for late payments; the host still owns the review/refund
  workflow and any decision to extend or replace an invoice.
* **Sending or custody.** This library does not hold keys, sign transactions,
  refund, sweep, or pay out. Adding those operations to a read-only receiving
  plugin would materially change its threat model.

## Supply chain

Zero dependencies, and the test suite fails if that changes: one test parses
every module and rejects any import outside an explicit stdlib allowlist,
another rejects any mention of a host framework. Both run against the
**installed wheel**, where the metadata is real.

`qrcodegen.py` is vendored unchanged from Project Nayuki (MIT). It is excluded
from the formatter so it stays byte-identical to upstream.

## Reporting

Do not open a public issue for a vulnerability that could direct funds
incorrectly or expose private data. Open a private advisory instead:

    https://github.com/dowoop/cryptopos-core/security/advisories/new

A private advisory rather than an address on purpose. It keeps the report
non-public until there is a fix, it does not depend on one person reading one
inbox, and it leaves a record attached to the repository rather than in a
mailbox. Ordinary hardening ideas and non-sensitive defects can use the public
issue tracker.
