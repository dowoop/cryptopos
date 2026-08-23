# The proof register

Every function in `cryptopos-core` and every control on the terminal, what it
is for, and what proves it.

This file is a reference. The *enforcement* is `make prove`, which reads its
inventory out of the source rather than out of this document — so a function
added tomorrow fails the gate whether or not anyone remembers to add a row
here. The register exists because a gate tells you that something ran and
never tells you what it was for.

```bash
make test       # the core suite alone, well under a second
make terminal   # the two terminal suites
make prove      # nothing unexecuted, nothing unclicked, nothing unexplained
make worth      # break it on purpose; fail if the suites do not notice
make check      # the lot, across four Pythons and against the built wheel
```

## What "proved" means here, exactly

Three questions, asked separately, because they are not the same question.

| gate | asks | how |
|---|---|---|
| `tools/prove.py` | did every line **run**? | line coverage from stdlib `trace` |
| `tools/prove.py` | is every symbol **explained**? | every symbol needs a row in this file |
| `tools/prove_terminal.js` | is every method and control **reached**? | the page's own source, diffed against what the suites dispatched |
| `tools/worth.py` | is every assertion **worth making**? | rewrite one operator or constant; fail if nothing fails |
| `tools/worth_terminal.js` | the same, for the page | the same, with a code/string mask so only real code is mutated |

The first three are a floor. Coverage catches the failure mode that is
otherwise invisible — code that is quietly dead, or a refusal branch nobody
has ever taken — and says nothing about whether the test around it asserts
anything. **That is what the last two are for.** A test that executes a line
and asserts nothing about it passes forever; the only way to find it is to
make the code wrong and see whether anything complains.

Every one of those failures was live in this tree, and each was found by the
gate that now guards it:

| found by | what was wrong |
|---|---|
| coverage | the three rate-feed adapters (`_coinbase`, `_kraken`, `_bitstamp`) had **never executed** — every rates test stubbed `FEEDS` wholesale, proving the policy around a price and nothing about the code that fetches one |
| coverage | `test_zcash_refuses_a_shielded_address…` passed against a fixture that was not decodable bech32, so it was refused one check earlier as "mistyped" and the branch it was named for never ran |
| reachability | every click handler on the terminal was unreachable under test: the jQuery stub answered `find()` with a no-op, so `wire()` registered handlers against an object that threw them away |
| reachability | `on_page_load`, the entry point Frappe itself calls, was never invoked by any test |
| mutation | **129 mutations of the core survived** — 48 of them in the rails table alone, where every chain ID, decimal count and settle gate could be changed at will |
| mutation | fixtures built from the constants they were meant to pin: `component_body` filled its slots from `chain.SLOT_*`, so a slot index could move and the read moved with it |
| mutation | `test_each_carries_the_feed_timeout` asserted the timeout against `FEED_TIMEOUT_SECONDS` itself — true for any value |
| mutation | **51 mutations of the terminal survived**, mostly `x \|\| ""` fallbacks: swapping one to `x && ""` renders the fallback in place of the URI, the reference or the timestamp, and nothing noticed |
| mutation (indirectly) | `charge()` noted "no rail selected" and **never re-rendered** — pressing Charge on a terminal with no rails did nothing whatsoever on screen. A refusal hidden by omission. Fixed. |
| mutation | `_permute` ended with `return lanes` that no caller read, and the squeeze indexed `lanes[i % 5][i // 5]` over four lanes where both terms are the identity. Both removed. |

Current state:

```
cryptopos_core   2,062 lines, 579 tests  100.0% executed   1,809/1,825 mutants killed (16 equivalent)
terminal          29 methods, 21 controls  240 checks    132/135 mutants killed (3 equivalent)
                                                         on 3.9 / 3.11 / 3.13 / 3.14, source and wheel
```

### Survivors are triaged, not banned

A mutation that cannot change observable behaviour is an *equivalent mutant*.
It is undetectable by construction, and "killing" it would mean asserting an
implementation detail rather than a behaviour. Both tools therefore carry an
`EQUIVALENT` list: one entry per accepted survivor, each stating why it cannot
be killed. Anything not on that list fails the gate.

That list is the honest part. A mutation score published without one is either
a lie or a suite full of assertions on trivia. All sixteen entries here were
checked rather than assumed — the accumulator seed over 3,000 random inputs,
the bech32 generator bound over 2,000, the `!` guard's redundancy over 30,000
strings, and the short-checksum case solved algebraically rather than searched.

---

# cryptopos-core

## `plugin.py` — immutable facts at the host/plugin boundary

| symbol | what it is for | proved by |
|---|---|---|
| `_identifier`, `_reference` | bounded ASCII identity grammars; URI punctuation cannot become part of a rail identity | `test_plugin.Identity`, `test_plugin_boundaries.IdentityBoundaries` |
| `Network`, `Network.__post_init__`, `Network.key` | a concrete, explicitly test/main network rather than an ambiguous mode | `test_plugin.Identity`, `IdentityBoundaries` |
| `Asset`, `Asset.__post_init__`, `Asset.key` | the on-network asset identity and exact atomic scale | `test_plugin.Identity`, `IdentityBoundaries` |
| `RecipientBaseline`, `RecipientBaseline.__post_init__` | provider, recipient, position and pre-payment facts captured before showing a request | `BaselineAndIntentBoundaries`, rail baseline tests |
| `PaymentIntent`, `PaymentIntent.__post_init__` | immutable sale identity, amount, lifetime and baseline binding | `test_plugin.Intents`, `BaselineAndIntentBoundaries` |
| `PaymentRequest`, `PaymentRequest.__post_init__` | bounded payer-facing URI and notice, with the exact requested atomic amount | `ReadinessReport`, `RequestAndTransferBoundaries` |
| `TransferObservation`, `TransferObservation.__post_init__` | one provider-reported transfer with coherent confirmation, block and amount facts | `RequestAndTransferBoundaries`, provider rail tests |
| `ObservationBatch`, `ObservationBatch.__post_init__`, `ObservationBatch.complete`, `ObservationBatch.require_intent`, `ObservationBatch.extend` | a bounded, resumable provider read that cannot cross providers, baselines, recipients or sales | `ObservationBoundaries`, EVM paging tests |
| `SettlementDecision`, `SettlementDecision.__post_init__`, `SettlementDecision.transaction_id` | one pure settlement state with atomic multi-transaction attribution; the singular property is display-only compatibility | `DecisionAndReadinessBoundaries`, split-payment tests |
| `Readiness`, `Readiness.__post_init__`, `Readiness.chargeable`, `Readiness.reason_for` | deployment-specific capability evidence without confusing “installed” with “usable” | `ReadinessReport`, `DecisionAndReadinessBoundaries` |
| `PaymentRail`, `PaymentRail.readiness`, `PaymentRail.capture_baseline`, `PaymentRail.validate_recipient`, `PaymentRail.create_request`, `PaymentRail.observe` | the dependency-free structural contract independently installed rails implement | `test_plugin.Registry`, `test_conformance`, packaging entry-point tests |

## `bitcoin.py` — Bitcoin Testnet 4 request, observation, and settlement

| symbol | what it is for | proved by |
|---|---|---|
| `_NoRedirect`, `_HttpsTransport` | bounded HTTPS GETs without redirects, cookies, credentials or ambient authentication | `test_provider_failures.BitcoinTransportFailures` |
| `_configuration`, `_read`, `_text`, `_json` | validate provider configuration and normalize bounded transport/encoding failures into documented provider errors | `BitcoinTransportFailures` |
| `_exact_nonnegative`, `_tip`, `_transactions` | exact provider integers plus a bounded address-history page | `BitcoinTransportFailures`, `BitcoinProviderDataFailures` |
| `_verified_provider` | pin block zero to the official BIP 94 Testnet 4 genesis before trusting any observation | `test_bitcoin.test_readiness_proves_genesis_and_tip`, wrong-network tests |
| `BitcoinTestnet4`, `BitcoinTestnet4.validate_recipient`, `BitcoinTestnet4.readiness`, `BitcoinTestnet4.capture_baseline`, `BitcoinTestnet4.create_request`, `BitcoinTestnet4.observe` | the complete fresh-address Testnet 4 charge path | `test_bitcoin.BitcoinRailTest` |
| `BitcoinTestnet4._intent`, `BitcoinTestnet4._parse_transfers`, `BitcoinTestnet4._spends_from_recipient` | bind observations to the sale, reject hostile Esplora shapes, and prevent the merchant's own change from becoming payment | `BitcoinProviderDataFailures`, change/output tests |

## `evm.py` — Sepolia and Amoy provider truth

| symbol | what it is for | proved by |
|---|---|---|
| `_NoRedirect`, `_JsonRpcTransport`, `_JsonRpcTransport.post` | bounded HTTPS JSON-RPC without redirects or ambient authentication | `test_provider_failures.JsonRpcTransportFailures` |
| `_configuration`, `_rpc`, `_quantity` | validate endpoints, bind JSON-RPC envelopes to request id 1, and accept only canonical exact quantities | `JsonRpcTransportFailures` |
| `EthereumSepoliaRail`, `EthereumSepoliaRail.validate_recipient`, `EthereumSepoliaRail.readiness`, `EthereumSepoliaRail.capture_baseline`, `EthereumSepoliaRail.create_request`, `EthereumSepoliaRail.observe` | native ETH or one ERC-20 on the concrete Sepolia chain, with bounded resumable observation | `test_evm.EthereumSepoliaTest`, `EvmProviderDataFailures` |
| `EthereumSepoliaRail._provider`, `EthereumSepoliaRail._verify_network`, `EthereumSepoliaRail._tip`, `EthereumSepoliaRail._probe_observation` | prove the chain id and the actual block/log methods a configured deployment needs | readiness and provider-data tests |
| `EthereumSepoliaRail._finalized_tip`, `EthereumSepoliaRail._is_mature`, `EthereumSepoliaRail._settled_reason`, `EthereumSepoliaRail._pending_reason` | Sepolia's three-confirmation policy hooks, overridden where Amoy needs finalized inclusion | settlement-policy tests |
| `EthereumSepoliaRail._receipt_success`, `EthereumSepoliaRail._native_transfers`, `EthereumSepoliaRail._token_transfers`, `EthereumSepoliaRail._block_timestamp` | receipt-, block-hash-, contract-, topic-, and timestamp-bound attribution from hostile provider data | `EvmProviderDataFailures`, native/token settlement tests |
| `EthereumSepoliaRail._verified_recipient`, `EthereumSepoliaRail._intent` | checksum and sale/rail binding before a request or observation | address and cross-intent tests |
| `PolygonAmoyUsdcRail`, `PolygonAmoyUsdcRail._finalized_tip`, `PolygonAmoyUsdcRail._is_mature`, `PolygonAmoyUsdcRail._settled_reason`, `PolygonAmoyUsdcRail._pending_reason` | replace confirmation counting with Polygon finalized-block inclusion | Amoy finality tests |

## `catalog.py`, `ootle.py`, `registry.py`, and `conformance.py`

| symbol | what it is for | proved by |
|---|---|---|
| `RequestRail`, `RequestRail.readiness`, `RequestRail.validate_recipient`, `RequestRail.capture_baseline`, `RequestRail.create_request`, `RequestRail.observe`, `RequestRail._intent` | truthful request-only adapters: build what is supported and raise instead of simulating observation or settlement | `test_catalog`, `RequestRailBoundaries` |
| `builtin_rails` | the explicit twelve-rail built-in catalog used by opt-in registration | `test_catalog.CatalogIdentity` |
| `OotleEsmeralda`, `OotleEsmeralda.validate_recipient`, `OotleEsmeralda.readiness`, `OotleEsmeralda.capture_baseline`, `OotleEsmeralda.create_request`, `OotleEsmeralda.observe` | observe XTR balance movement while refusing payer-URI and transaction-attribution claims | `test_ootle`, `OotleBoundaries` |
| `OotleEsmeralda._reader`, `OotleEsmeralda._network`, `OotleEsmeralda._intent` | bind reads to one Esmeralda indexer, monotonic epoch, and payment intent | `OotleBoundaries` |
| `validate_plugin`, `RailRegistry`, `RailRegistry.register`, `RailRegistry.keys`, `RailRegistry.discover`, `RailRegistry.register_builtins` | validate identity, capabilities, and initial/resumed method call shapes; discover and deduplicate independently installed rails with no import-time side effects | `test_plugin.Registry`, `RegistryBoundaries`, packaging discovery tests |
| `conformance_issues`, `require_conformant` | convert third-party structural/readiness mistakes into stable host-visible findings | `test_conformance`, `ConformanceBoundaries` |

## `rates.py` — a rate is a number, a source, and a time

| function | what it is for | proved by |
|---|---|---|
| `_urlopen`, `_NoDowngradeRedirects.redirect_request` | the single HTTPS-only door to a feed; the reason "no test here touches a network" is checkable rather than hoped for | `test_feeds.TheDoor` |
| `_read_json` | GET and parse under the feed timeout — a hung vendor must not hang a sale | `test_feeds.EveryAdapter` |
| `_coinbase` | Coinbase spot price; uppercase asset in the path | `test_feeds.Coinbase` |
| `_kraken` | Kraken last-trade; **calls Bitcoin XBT**, and answers under a pair name it picks rather than the one asked for | `test_feeds.Kraken` |
| `_bitstamp` | Bitstamp ticker; lowercase pair, trailing slash or 404 | `test_feeds.Bitstamp` |
| `_price_from` | a vendor's string → `Decimal`, exactly; `None` if unusable | `test_rates.Microcents` |
| `_median` | the middle of three, so one bad feed cannot move the price | `test_feeds.Median` |
| `_spread` | widest disagreement as a fraction, which is what real money is refused on | `test_feeds.Median`, `test_rates.RealMoneyRules` |
| `_fetch_price` | isolate one adapter so its failure cannot cancel the independent answers | `test_feeds.EveryAdapter` |
| `_gather` | ask every feed concurrently, keep registration order, swallow the ones that did not answer | `test_feeds.EveryAdapter`, `test_rates.Quote.test_feeds_are_asked_concurrently_and_reported_in_registration_order` |
| `quote_detailed` | the full account: number, who said so, when, and the spread | `test_rates.QuoteDetail` |
| `quote` | the same with three fields pulled out, for a caller that only wants the number | `test_rates.Quote`, `RealMoneyRules`, `Constants` |
| `native_for` | cents → exact native units, integer arithmetic throughout. **The primitive, not the charge path** — use `rails.invoice_amount` to price a sale | `test_rates.NativeForArithmetic`, `NativeForRefusals` |

The three real-money rules — no demo fallback, no lone feed, no wide
disagreement — are `test_rates.RealMoneyRules`. All three raise
`RateUnavailable` or its subclass, so a host that already catches the base
class stays safe without being changed.

## `addresses.py` — the last check before money moves

The verdict is three-valued (`ok` / `refused` / `unchecked`) and that is the
point: an unchecked address must not be indistinguishable from a checked one.

| function | what it is for | proved by |
|---|---|---|
| `validate` | the entry point: checksum **and** network binding, for a rail in a mode. Never raises | every class below, plus `test_addresses.Totality` |
| `to_eip55` | upgrade a lowercase EVM address into its checksummed form, once, when an operator saves it | `test_addresses.EvmAddresses` |
| `keccak256` | EIP-55 needs Keccak, and `hashlib.sha3_256` **is not it** — one padding byte apart, every digest different | `test_addresses.KeccakVectors` |
| `_rotl`, `_permute` | Keccak-f[1600] internals | via `KeccakVectors`' published digests |
| `_b58_decode` | base58 → bytes, `None` on a character outside the alphabet | `Base58CheckEncoder`, `SolanaAddresses` |
| `_b58check_decode` | version + payload if the double-SHA256 checksum verifies — the check a regex cannot do | `Base58CheckEncoder`, `BitcoinAddresses`, `Base58CheckLength` |
| `_bech32_polymod`, `_bech32_hrp_expand` | the bech32 checksum itself | `Bech32Decoder` (encode/decode round trip) |
| `_bech32_decode` | hrp, data and **which spec** — bech32 vs bech32m is what BIP-350 keys its rule on | `Bech32Decoder`, `Bech32Boundaries` |
| `_convert_bits` | 5↔8 bit repacking, under both padding rules; non-zero filler is refused so an address stays canonical | `ConvertBits`, `ConvertBitsBoundaries` |
| `_segwit_decode` | witness version and program, with every BIP-173/350 length and spec rule | `SegwitDecoder`, `SegwitBoundaries` |
| `_monero_b58_decode` | Monero's **block-based** base58 — a standard decoder produces garbage here | `Monero`, `MoneroPrefixes`, `MoneroEncoding` |
| `_read_varint` | Monero's network prefix, which can be genuinely unreadable | `Varint`, `VarintBoundaries` |
| `_check_bitcoin_like` | btc and dash: segwit then base58, keyed on the **rail** and never the family | `BitcoinAddresses`, `DashAndZcash`, `VersionBytes` |
| `_check_zcash` | two-byte transparent versions; a shielded address is refused as *shielded*, not as *mistyped* | `DashAndZcash` |
| `_check_monero` | keccak checksum, then the prefix table; stagenet is named rather than lumped in | `Monero`, `MoneroPrefixes` |
| `_check_evm` | EIP-55 if the case carries a checksum, `unchecked` if it does not | `EvmAddresses`, `EvmWellFormedness` |
| `_check_solana` | 32 bytes and no checksum exists, so `unchecked` — saying `ok` would overclaim | `SolanaAddresses` |

**Network binding is half the value.** A mainnet URI carrying a testnet
address is well-formed, scannable, and sends real money into a hole. Bitcoin,
Dash, Zcash and Monero encode their network and the mismatch is refused with
words that name it. EVM and Solana do not, and this module says so instead of
implying a check it did not perform (`EvmAddresses`, `Unverifiable`).

## `chain.py` — the policy tier, read without an account and without a fee

Total by contract: **a sale must never fail because the policy layer is
down.** Every method returns a sentinel and a reason, for every shape of
failure, always — `test_chain.Totality`.

| function | what it is for | proved by |
|---|---|---|
| `OotleReader` | the reader itself: one object, configured at construction, that reads the policy tier keylessly and feelessly | `Construction` |
| `OotleReader.__init__` | takes its configuration at construction, so the same reader works from a till, a backend or a bare script | `Construction` |
| `OotleReader._get` | one GET, returning `(body, None)` or `(None, reason)`; refuses plain http before touching the network | `Availability`, `TransportSecurity` |
| `OotleReader.available` | is the policy layer reachable at all | `Availability`, `ShapesThatAreNotTheContract` |
| `OotleReader.promise` | the deployed contract's own account of itself — rate, ceilings, resources | `Promise`, `Provenance`, `SlotLayout`, `ShapesThatAreNotTheContract` |
| `OotleReader.points_balance` | a customer's balance. **Zero and unreadable are different answers** and are returned differently. Never on the path of a sale | `PointsBalance`, `TheVaultRead` |
| `OotleReader.resource_balance` | a public account's balance for an explicitly named resource, used by the XTR observer without granting transaction attribution | `test_ootle`, `OotleBoundaries` |
| `OotleReader.check_it_yourself` | the literal URLs a customer can open to check the promise themselves | `Wording` |
| `_NoDowngradeRedirects` | a redirect handler installed **unconditionally**, including for `allow_insecure` readers — it only fires when the request was https, so one opener has one behaviour and nothing can be configured wrong | `TransportSecurity` |
| `_NoDowngradeRedirects.redirect_request` | follow redirects, never https → http. Without it the scheme check is decoration | `TransportSecurity` |
| `_urlopen` | the single door, built once and reused, with the redirect handler installed unconditionally | `TheOpener` |
| `_default_user_agent` | an indexer operator gets to see who is reading | `Construction.test_identifies_itself` |
| `_hex_of` | pull a resource id out of a CBOR-ish slot | via `Promise`, `VaultWalk` |
| `_amount` | a slot → an integer, or `None`. Returns rather than raises, because a raise here escapes `promise()`, which is documented never to raise | `AmountDecoding` |
| `_exact_integer` | accept integer-shaped indexer values without truncating booleans, fractions, or malformed text | `AmountDecoding`, hostile-shape tests |
| `_walk_for_resource` | find the vault that is the **sibling** of a resource id, bounded in depth so a deep body cannot overflow a request handler | `VaultWalk`, `WalkDepth` |
| `_balance_of` | a vault body → a balance, or a reason | `PointsBalance`, `ZeroIsABalance` |
| `ceilings_wording` | every limit that must ship on the surface that offers the feature | `Wording` |
| `earning_only_notice` | the single claim an operator is most likely to get wrong: **spending does not work** | `Wording` |

## `rails.py` — what the terminal knows about each chain

12 rails across 8 chain families, all testnet, carried across whole with their
maturity notes. Table shape is asserted rather than assumed —
`test_rails.TableShape`.

| function | what it is for | proved by |
|---|---|---|
| `rail_for` | the table entry. **Raises `KeyError` rather than returning `None`** — a `None` that reaches a pricing call becomes a sale charged against nothing | `Lookup` |
| `rail_keys` | every rail key, in table order | `Lookup` |
| `token_contract_for` | USDC's contract per network; only literal `"testnet"` gets the test deployment | `PerNetworkIdentity`, `ContractAddresses` |
| `token_mint_for` | the same for Solana's mint | `PerNetworkIdentity` |
| `earns_policy_points` | may a sale on this rail earn loyalty points | `PolicyPoints` |
| `price_asset` | which asset's price prices this rail — its own, unless it says otherwise | `PriceAsset` |
| `rail_demo_microcents` | the hardcoded demo rate in the precise unit. **Never prices real money** | `UnitMath` |
| `usd_cents_to_native` | cents → native, rounding **once** at display precision then scaling up | `UnitMath` |
| `native_to_usd_cents` | the inverse, via the same two steps so the pair cannot disagree | `UnitMath` |
| `format_amount` | native integer → the human string a URI carries | `Decimals`, `Representability` |
| `representable_amount` | the largest amount at or below this one that `format_amount` writes exactly | `Representability` |
| `is_exactly_displayable` | can this amount be written without losing anything | `Representability` |
| `invoice_amount` | **price sales with this.** Exact, and always statable in a URI | `Representability` |

`GoldenTable` pins the table's **values** — every rail's decimals, settle
gate, chain ID, demo rate and simulator pacing, as literals. `TableShape`
asserts the shape and nothing else, and mutation testing found the cost of
that: 48 separate edits to this table left the whole suite green, including
Polygon's chain ID and Ethereum's 18 native decimals. A rail added without a
row in `GoldenTable` fails the suite.

`TwoConversionsThatDisagree` is the one to read first: `rails.invoice_amount`
and `rates.native_for` do not agree, the difference is a defect in one
direction, and the test names which.

## `uri.py` — the exact string the QR encodes

| function | what it is for | proved by |
|---|---|---|
| `build_uri` | the payment URI for 11 of the 12 rails, refusing rather than emitting one that cannot settle | `Bitcoin`, `Ethereum`, `Solana`, `Monero`, `Tari`, `Zcash`, `Coverage` |
| `_base58_decode` | decode a Solana reference without admitting characters outside the public-key alphabet | `Solana.test_the_binding_reference_must_be_a_public_key` |
| `_solana_reference` | require the sale-binding reference to be a 32-byte public key before it reaches the query string | `Solana` |
| `base58_encode` | Solana reference keys | `Base58` |
| `fresh_32_bytes` | a deterministic-per-sale 32-byte value | `Solana` |

Three guards, and all refuse rather than return something:

- `AddressGuard` — an address whose verdict is `refused` never reaches a QR,
  and `unchecked` is refused on mainnet specifically.
- `AmountGuard` — a decimal-amount scheme that cannot state the invoiced
  amount exactly raises `AmountNotRepresentable` rather than emit a truncated
  one. The customer would pay exactly what they were shown and the sale would
  sit short of its own invoice forever.
- `Solana` — the reference that binds the payment to its sale is a public key,
  not arbitrary query-string text.
- `Zcash` — a transparent ZIP-321 URI never carries a memo, which the
  standard forbids for that recipient type.

## `qr.py` — a payment URI as a module grid

| function | what it is for | proved by |
|---|---|---|
| `modules_for` | encode to a grid of `"0"`/`"1"` rows plus the quiet zone, at medium error correction | `test_qr.Shape`, `QuietZone`, `FinderPatterns` |

What crosses the wire is **the grid, not markup**: Frappe's sanitiser strips
`d` and `fill` from stored SVG, which yields a well-formed and completely
blank image. `SameEncoderEverywhere` pins that there is exactly one encoder.

## `modes.py` — one vocabulary at every money boundary

| function | what it is for | proved by |
|---|---|---|
| `require_mode` | refuse an unknown mode before a typo can weaken mainnet pricing, identity or URI policy | `test_rates.Quote`, `test_rails.PerNetworkIdentity`, `test_uri.Coverage` |
| `address_network` | map demo to the documented mainnet-shaped address family and testnet only to testnet | `test_uri.AddressGuard`, `test_addresses.Totality` |

## `errors.py` — the conditions that stop a sale

Pricing and addressing raise rather than return, because there is no honest
sentinel: a caller handed `None` will either display it or multiply by it.

| error | carries | raised by | proved by |
|---|---|---|---|
| `CryptoPosError` | — | base of all of these; catch this to catch all | `test_packaging.PublicSurface` |
| `RateUnavailable` | `asset` | no usable price | `test_rates.Quote` |
| `FeedsDisagree` | `prices`, `spread` | feeds too far apart to price real money. **A subclass on purpose** — a host catching the base class keeps refusing without being changed | `test_rates.RealMoneyRules` |
| `InvalidRate` | `rate_microcents` | a zero or negative rate reached a conversion | `test_rates.NativeForRefusals` |
| `InvalidAmount` | `field`, `value` | a non-positive or lossy amount reached a money boundary | `test_rates.NativeForRefusals`, `test_uri.AmountGuard` |
| `InvalidAsset` | `asset` | a missing, non-text, blank, or URI-unsafe asset ticker reached the quote boundary | `test_rates.Quote` |
| `InvalidMode` | `mode`, `valid_modes` | an unknown mode reached a money boundary | `test_rates.Quote`, `test_uri.Coverage` |
| `InvalidPaymentIdentity` | `rail_key`, `field`, `value`, `reason` | sale-binding data cannot safely be represented in the rail's URI | `test_uri.Solana` |
| `UnsupportedRail` | `rail_key` | a rail is unknown or has no standardized payment URI implementation | `test_uri.Coverage` |
| `_coerce_integer` | exact integer or `None` | accepts integer form fields without silently truncating floats or treating booleans as money | `test_rates.NativeForRefusals` |
| `AddressRefused` | `verdict`, `reason` | the receiving address failed its check; `verdict` distinguishes provably-wrong from unverifiable | `test_uri.AddressGuard` |
| `AmountNotRepresentable` | `representable` | the amount cannot be written exactly; `representable` is what to invoice instead | `test_uri.AmountGuard` |
| `RailPluginError` | — | common base for discovery, capability, provider and structural rail failures | `test_plugin.Registry`, packaging public-surface tests |
| `InvalidRailPlugin` | `reason` | malformed plugin objects or cross-intent/cross-provider contract values | contract and registry boundary tests |
| `DuplicateRail` | `rail_key` | two installed plugins claim one concrete rail identity | `test_plugin.Registry.test_duplicate_concrete_rail_is_refused` |
| `RailNotInstalled` | `rail_key` | the host requested a concrete rail that discovery did not supply | `test_plugin.Registry.test_missing_rail_has_a_documented_error` |
| `UnsupportedCapability` | `rail_key`, `capability` | a request-only or observation-only rail was asked to invent an unsupported operation | `test_catalog`, `test_ootle` |
| `RailProviderError` | `rail_key`, `reason` | a configured provider is unavailable, malformed, unsafe, or on the wrong network | provider failure suites |

## Packaging

`test_packaging` asserts the properties that make this worth publishing: no
non-stdlib imports, no declared dependencies, no `frappe` anywhere in the
source, the vendored notice intact, and `__all__` matching what is actually
exported. These only mean something against installed metadata, so they skip
from a source tree and run for real in `make wheel`.

---

# The terminal

`/app/terminal`, or the **Terminal** shortcut on the CryptoPoS workspace.

Two suites, and the split matters: `terminal_render_test.js` proves what each
state **looks like**; `terminal_button_test.js` proves that clicking it
**does** something. A button whose handler was never attached renders
identically to one that works, which is exactly how the event layer went
untested while the suite was green.

## Controls

Every row below is clicked, changed or typed at by the button suite, and
`tools/prove_terminal.js` fails if any stops being.

| control | what it does | proved by |
|---|---|---|
| `[data-key]` × 12 | digits, `.`, backspace. Max 9 digits, max 2 decimal places, refused at the key rather than dropped later | §1 |
| `.cpos-rail` | chooses the rail; re-renders so the **gate text and maturity warning follow the choice** | §2 |
| `[data-act="charge"]` | the one button that spends money. Disabled with no amount, says *Charging…* and disables itself in flight so a double press cannot be a double sale | §3 |
| `[data-act="poll"]` | single-steps the heartbeat — the same function the `* * * * *` cron calls, so the timer and the button cannot drift apart | §5 |
| `[data-act="autopoll"]` | starts and stops a 10-second interval; a sale that ends on a tick stops its own timer | §7 |
| `[data-act="cancel"]` | abandons a live sale (**Cancel**) and leaves an ended one (**New sale**) — one control, two words | §8 |
| `[data-act="points"]` | expands and collapses the loyalty ceilings and the check-it-yourself URLs | §9 |
| `[data-act="dismiss"]` | dismisses a held refusal | §4 |
| `[data-act="bench"]` | opt-in developer panel; **off on first open** | §10 |
| `[data-act="log"]` | opt-in activity log; off on first open, and **opening it is what clears a held refusal** | §10, §11 |

## The keyboard

A cashier keys a number and takes money for it; the real keyboard has to work.

| key | behaviour | proved by |
|---|---|---|
| `0`–`9`, `.` | key the amount | §12 |
| `Backspace` | remove a character | §12 |
| `Enter` | charges, polls, or starts a new sale **depending on where the sale is** — all three branches | §12 |
| `Escape` | abandons a live sale, or clears the amount when there is none | §12 |
| anything else | left to the browser; swallowing every keypress would break refresh, find and tab | §12 |
| any key, inside a form control | ignored, so typing in a field is not hijacked by the keypad | §12 |

## Methods

All 29 run. The ones worth naming:

| method | what it is for | proved by |
|---|---|---|
| `on_page_load` | **the desk's actual entry point.** Every other check constructs the class directly, which skips the one function Frappe invokes | §15 |
| `charge` | the one write that starts a sale; a refusal is put on the terminal whatever the log is doing | §3, §4 |
| `poll` | advances the sale; `silent` suppresses the notice for timer-driven polls only | §5, §6, §7 |
| `note_error` | **the rule that makes hiding the log allowable** — a disclosure may hide an explanation and never a refusal | §11 |
| `reason_from` | unwraps the server's own words out of `_server_messages` rather than replacing them with something generic | §4, §6 |
| `wire` | re-attaches every handler after each render. The page rebuilds its whole body, so this running again is the only reason anything works on the second screen | §13 |
| `is_terminal` | which of the eight states are endings — four of them | §5, §8 |
| `qr_svg` | draws the bits the server encoded; there is still exactly one encoder | §3 |
| `fmt_native` | BigInt, because satoshis fit in a double and wei does not | render §6 |
| `load_loyalty` | never on the path of a sale — runs only once a sale has ended, so a dead policy layer can delay a disclosure and never delay taking money | §5 |

## The four endings, and what each is allowed to claim

Rare at the counter by construction, which is exactly why they are pinned
(render §3):

| ending | says | never says |
|---|---|---|
| `confirmed:clean` / `over` | SETTLED, and names an overpayment as one | |
| `expired:clean` | EXPIRED — the lock ran out, nothing arrived | |
| `expired:under` | PART PAID | |
| `needs_review:unidentified` | money was sighted and could not be tied to this sale | that it was paid |
| `needs_review:unverified` | *the last look never reached the chain, so this cannot be called either way* | "expired, unpaid" — a claim about the world the observation did not support |

---

# What is still not proved

Stated plainly, because a register that only lists green rows is worth less
than one that says where it ends.

- **No browser has run this page.** Both terminal suites run against a stubbed
  desk with a small hand-written DOM. Layout, focus, CSS and real event
  bubbling are not exercised, and no `drive_gui.py` equivalent measures what
  is actually visible.
- **Mutation testing proves the assertions bite. It does not prove they are
  the right assertions.** A suite can be mutation-tight around a rule that is
  itself wrong: if `gate_confs` for Bitcoin should be 6 rather than 3, the
  golden table pins 3 and every mutant dies. What these gates rule out is code
  nobody is watching. What they cannot rule out is everybody watching the
  wrong thing — that is what the on-chain confirmations in
  `DESIGN_REGISTER.md` and the two live harnesses are for.
- **The mutation operators are a fixed set**: comparison and boolean swaps,
  arithmetic swaps, integer and boolean constants, dropped negations, and
  `return None`. A defect no such edit produces — a wrong algorithm, a missing
  branch, an absent feature — is outside what either tool can see. String
  constants are deliberately not mutated in the core; the wording of a refusal
  is asserted directly instead, wherever the wording is the point.
- **The Frappe half is not measured here.** `charge.py`, `watch.py`,
  `settle.py`, `api.py` and the doctypes need a bench; their proofs are
  `cryptopos.harness.run` (31 checks) and `cryptopos.harness_loyalty.run` (43
  checks), which need the Docker stack and a live testnet4 chain.
- **`qrcodegen.py` is excluded from the line gate** and deliberately: it is
  vendored unchanged, and covering its unused branches would mean writing
  tests for code this package must not edit. `test_packaging.VendoredGenerator`
  checks the notice survived.
- **No award has been minted end to end.** `issued` is the one award state
  never observed against a real account on chain.
