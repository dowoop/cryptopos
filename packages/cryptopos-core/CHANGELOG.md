# Changelog

## 1.1.0

The package is now an installable payment-rail kernel rather than only a set
of point-of-sale helpers.

* Added the `PaymentRail` protocol, concrete `Network` and `Asset` identities,
  immutable payment/observation/settlement values, entry-point discovery, a
  built-in registry, readiness reporting, and host-side conformance checks.
* Added complete, provider-verified testnet rails for Bitcoin Testnet 4,
  Sepolia ETH, Sepolia USDC, and Amoy USDC.
* Bound recipient baselines and provider observations to the exact payment
  intent, recipient, provider, and starting chain position before settlement.
* Bounded EVM observation is resumable and cumulative rather than becoming
  permanently unavailable after a long invoice or provider outage.
* Settlement returns every credited transaction ID for atomic host claims and
  routes payments after the host-supplied expiry to review.
* ERC-20 observations now verify and return the canonical transfer-block time.
* EVM readiness exercises the actual full-block or token-log method required by
  the rail, and Monero no longer claims Stagenet address validation before the
  validator can express that network.
* Added a truthful twelve-rail catalog. Request-only and observation-only
  adapters say exactly which capability is unavailable rather than simulating
  a successful charge path.
* Split Ootle payment observation from loyalty policy reads. The payment rail
  consumes the indexer's vault-filtered deposit SSE replay, carries its
  monotonic event ID as an exact resumable cursor, attributes exact amounts to
  transaction IDs, uses UTC `finalized_at` for expiry, and settles committed
  deposits without a confirmation-depth gate. Its request remains an account
  address with an explicit notice because no registered Ootle payment URI
  exists.
* Demo payment requests now use test deployments, addresses, chain IDs, and
  Esmeralda authorities. Only explicit `mainnet` selects mainnet identity.
* Hardened oversized address input, canonical Monero prefix encoding, hostile
  finite price values, lossy policy integers, provider response bounds, HTTPS
  endpoints, network identity checks, ambient provider proxies, and vault-map
  traversal.

## 1.0.0

The release that hardened the original helper APIs at their money boundaries.
It did not make every catalog rail chargeable or remove the host and provider
trust boundaries documented in `SECURITY.md`. Everything below is a behaviour
change; four of them are breaking, and each one breaks in the direction of
refusing rather than proceeding.

### Addresses are checked now (new `addresses` module)

Nothing verified a receiving address before this release. `build_uri` embedded
whatever string it was handed, so a mistyped or wrong-network address produced
a perfectly scannable QR pointing at a key nobody holds.

* Bech32 and Bech32m (BIP-173 / BIP-350), Base58Check, EIP-55, Monero's
  block-based base58 with its Keccak checksum — all implemented against
  published vectors, all pure stdlib.
* **Network binding.** A testnet address on a mainnet sale is refused, and the
  refusal says which way round it is. Bitcoin, Dash, Zcash and Monero encode
  their network; EVM and Solana do not, and this module says so rather than
  implying a check it did not perform.
* **Three-valued verdict** — `ok`, `refused`, `unchecked`. `unchecked` is not
  a soft `ok`: Solana carries no checksum and Tari's format is unspecified, so
  claiming those were verified would make the whole verdict worthless.
* Ships `_keccak.py`, because `hashlib.sha3_256` is NIST SHA-3 and EIP-55
  needs original Keccak-256. They differ by one padding byte, which is enough
  to make every checksum wrong.

### Truncated URI amounts are refused (BREAKING)

A decimal-amount URI carries the display form, which truncates. Invoicing a
Solana sale through `rates.native_for` produced a QR asking for 73266000
lamports against an invoice of 73266666 — the customer pays what they were
shown, the sale sits 666 short of itself, and no amount of waiting fixes it.

* `build_uri` raises `AmountNotRepresentable` rather than emitting such a URI,
  and names the amount to invoice instead.
* New `rails.invoice_amount` is the charge-path entry point; it satisfies the
  invariant by construction. `rails.is_exactly_displayable` and
  `rails.representable_amount` expose the check.
* `rates.native_for` is unchanged and is now documented as the primitive it
  always was — correct for arithmetic, not for invoicing.

### Real-money pricing is held to three extra rules (BREAKING)

In `mainnet`, `quote()` now refuses to:

* price from `DEMO_MICROCENTS` — a hardcoded $64,000 BTC may price a demo and
  never real money;
* price from a single feed — one endpoint answering is not corroboration;
* price when feeds disagree by more than `MAX_FEED_SPREAD` (2%).

All three raise `RateUnavailable` or the new `FeedsDisagree`, **which
subclasses it** — a host already catching `RateUnavailable` keeps refusing
correctly with no change. Other modes are unaffected.

Also: three feeds instead of two, median instead of mean, prices parsed as
`Decimal`, and a new `quote_detailed()` carrying the time, the per-feed prices
and the measured spread.

### Policy reads refuse insecure transport (BREAKING)

* `OotleReader` refuses a non-`https://` indexer unless `allow_insecure=True`.
* Redirects from https to http are refused outright — without that the scheme
  check is decoration, since urllib follows a downgrade without comment.
* `promise()` facts now carry `indexer`. The default indexer is a **testnet**
  one (no mainnet policy tier is published), so a surface that shows a ceiling
  without its provenance is dropping it rather than not being given it.
* The module reaches the network through one seam, `chain._urlopen`.

### Fixed

* Non-finite feed values (`NaN`, infinities) are discarded instead of escaping
  the documented error model.
* Feed requests run concurrently, so quote latency is bounded by the slowest
  vendor timeout rather than the sum of all vendor timeouts.
* HTTPS price-feed redirects cannot downgrade to plaintext.
  Redirect schemes are normalized and every non-HTTPS destination is refused,
  including uppercase `HTTP` and non-web schemes.
* Monero validation binds each standard, integrated, and subaddress prefix to
  its exact decoded length. A checksum-valid arbitrary blob is not an address.
* Feed and indexer bodies have explicit size ceilings, so a broken endpoint
  cannot choose the terminal's memory use. Indexer URLs also refuse embedded
  credentials, queries, fragments, and non-HTTP schemes.
* Non-positive, boolean and lossy floating-point amounts/rates are refused at
  every public conversion and URI boundary.
* Invalid asset identifiers and unsupported URI rails now raise documented
  `CryptoPosError` subclasses rather than leaking built-in exceptions.
* Solana sale references are verified as 32-byte public keys before they enter
  the query string.
* Transparent Zcash URIs no longer carry a memo; ZIP-321 requires parsers to
  reject that combination. Their fresh address is the sale binding.
* Unknown mode strings are refused everywhere instead of being interpreted
  differently by pricing, address and URI code.
* Policy reads preserve malformed-vault-versus-zero balance semantics and
  remain total across malformed response headers and configuration types.
* The rail table and its rows are immutable after import.
* `USDC_ON_POLYGON` was not EIP-55 valid — a lowercase `c` where the checksum
  wants `C`. The bytes were right, so transfers would have worked; the point
  is that a hand-transcribed address nothing verified is how the same slip
  reaches a recipient address, where it is not recoverable. Every EVM address
  in the rails table is now checked by the suite.
* `make fmt` would rewrite 774 lines of the vendored `qrcodegen.py`. The
  linter honoured its exclusion; the formatter reads a different list.

### Compatibility

`build_uri(..., strict=False)` restores permissive **address** behavior off
mainnet. It never disables mode, amount, representability, or sale-binding
checks. **Mainnet ignores it** — a flag that skips the last address check before
real funds move is one that will eventually be passed by accident.

## 0.1.0

Extracted from the Frappe app: `rates`, `qr`, `chain`, `errors`, and the
vendored `qrcodegen`. Later joined by `rails` (12 rails, 8 families) and
`uri` (one branch per scheme).
