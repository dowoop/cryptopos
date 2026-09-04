"""Rails — what the terminal knows about a chain, as one table.

Pure data and pure functions: unit math and URI text, no network and no
state. Every field here answers a question some surface asks, and they sit
side by side so a drift between them is a one-screen read rather than a
scavenger hunt.

Carried across from the tkinter terminal's `rails.py` unchanged, including
the maturity notes. A rail that says `works` and a rail that says `partial`
are making different promises to the operator, and dropping the distinction
on the way across would be the first overclaim of the port.

    works       real testnet reads AND a real payer
    partial     real testnet reads, but no in-app payer
    sim-always  no free public endpoint in ANY network mode

Two decimals per rail, and the pair is the point:

    display_decimals   what a human sees ("0.001953 BTC")
    native_decimals    what the chain counts (sats, wei, lamports, ...)

The invoiced amount is computed ONCE at display precision, then scaled to
native, so small amounts do not round to zero. That one native integer is
the single source of truth for the URI, the screen and the watcher.

``binding_category`` answers a narrower question than the neighboring prose:
does the rail bind money to one sale even when the host derives no address?
``unconditional-per-sale`` says yes; ``not-unconditional`` says no. A host can
strengthen the latter by deriving a fresh receiving address, but cannot infer
the former merely by looking for an xpub in its own configuration.

**It is a claim about an ADAPTER, not about a chain.** D33 is why: Solana Pay's
reference was a sound protocol mechanism the whole time, and the rail still
credited the wrong sale until the adapter decoded the transfer instruction
rather than a balance delta. A chain that *could* support a per-sale binding
has not made one. Two things must be true and both live in code: the rail gives
each sale an identity of its own (`catalog.REFERENCE_RAILS`, or a host-derived
address), and its observer attributes the amount to the thing carrying that
identity. A rail with no observer has neither, so it declares
``not-unconditional`` — and that matters because
`cryptopos.catalog.declared_binding_category` lends a built-in declaration to
any installed plugin that declares none, and `api.rails`, `tools/rails_probe`
and `tools/snapshot` all turn it into an operator-facing "per-sale".

**What is deliberately NOT here.** Which endpoint an operator configured, and
whether a rail is switched on. Those are host questions -- they change per
deployment, they are edited by someone with a login, and a library that owned
them would be a library fighting whatever it was embedded in. `testnet_url`
and `live_url` below are the *published public* endpoint for a rail, which is
a fact about the chain; an operator's override sits above this table, not in
it.

The endpoint ladders, the measured finality tables, the watchability
arithmetic and `chargeable_in_mode()` also stay out, for a plainer reason:
they read operator config and measured timing state, and none of them can be
confirmed without the host they came from.

**A note on the comments below.** They are carried over verbatim, and several
cite things outside this package -- `watchers.py`, `simulator.py`,
`pos_actions`, `harnesses/`, `CHARTER.md`, `rates.unpriced_assets()`, "the
vault". Those all name parts of the tkinter terminal this table came from.
They are kept because each one records WHY a number is what it is, and a
measured value with its provenance stripped off is just a magic number. Read
them as citations, not as imports.
"""

from types import MappingProxyType

from .errors import InvalidAmount, InvalidRate, _coerce_integer
from .modes import MAINNET, require_mode
from .plugin import NOT_UNCONDITIONAL, UNCONDITIONAL_PER_SALE
from .rates import MICROCENTS_PER_USD

# ERC-20 USDC contract addresses (mainnet).
#
# EVERY ADDRESS HERE IS EIP-55 CHECKSUMMED, and `test_rails.ContractAddresses`
# fails if one stops being. `USDC_ON_POLYGON` arrived with a lowercase `c`
# where the checksum wants `C` (`...d8cc03...` for `...d8cC03...`) -- the
# bytes were right, so a transfer would have worked, but a hand-transcribed
# address that nothing verified is exactly how the same slip reaches a
# RECIPIENT address, where it is not recoverable.
USDC_ON_ETHEREUM = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
USDC_ON_POLYGON = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"
# SPL USDC mint on Solana:
USDC_MINT_SOLANA = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

# The TESTNET counterparts -- Circle's own test deployments. STALENESS HEDGE:
# test networks reset and test tokens get redeployed, so these are "true when
# last checked" (last checked 2026-07-23, all three answering), not permanent
# facts. The symptom of a moved contract is an EMPTY eth_call answer.
USDC_ON_SEPOLIA = "0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238"
USDC_ON_AMOY = "0x41E94Eb019C0762f9Bfcf9Fb1E58725BfB0e7582"
USDC_MINT_DEVNET = "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU"

RAILS = {
	# -- key ------------------------------------------------------------
	"btc": {
		"label": "Bitcoin / BTC",
		"chain": "Bitcoin", "asset": "BTC",
		"family": "bitcoin",              # which watcher + simulator personality
		"unit_name": "satoshi",
		"display_decimals": 8, "native_decimals": 8,
		"rate_cents": 64_000 * 100,       # $64,000 (vault demo rate)
		"gate_confs": 3,
		"testnet_gate_confs": 1,          # vault Bitcoin note: 3 mainnet / 1 testnet
		"gate_text": "confs >= 3 (mainnet; testnet settles at 1)",
		"binding": "fresh HD address per sale (from merchant xpub, BTCPay pattern)",
		"binding_category": NOT_UNCONDITIONAL,
		"maturity": "partial",
		"maturity_note": "real testnet4 reads + fresh HD addresses;"
							" no in-app payer - the faucet plays the customer",
		# live_url serves MAINNET mode, testnet_url serves TESTNET mode;
		# None means "no free public endpoint - the simulator answers,
		# and the log says so". testnet_name is the test network's
		# proper name (chains name their testnets like pets).
		#
		# Bitcoin's testnet endpoint is NOT JSON-RPC: it's an Esplora
		# REST explorer API (the indexer pattern the vault names).
		# testnet_transport tells watchers.py to use call_rest there;
		# demo keeps teaching the Bitcoin Core RPC dialect.
		#
		# real_transport (this rail's and every other's) describes what
		# a PRODUCTION deployment would ride - WS subscriptions for
		# latency plus polling for truth. This build implements only
		# the polling half; the string names the real-world pattern,
		# not a layer that exists here, and it is only ever printed as
		# "real transport would be: ..." when the simulator answers.
		"live_url": None,
		"testnet_url": "https://mempool.space/testnet4/api",
		"testnet_transport": "esplora-rest",
		"testnet_name": "testnet4",
		"real_transport": "Electrum/Esplora WS + RPC polling (no free public JSON-RPC)",
		"sim_block_seconds": 15, "real_block_time": "~10 min",
	},
	"eth": {
		"label": "Ethereum / ETH",
		"chain": "Ethereum", "asset": "ETH",
		"family": "evm-native",
		"unit_name": "wei",
		"display_decimals": 6, "native_decimals": 18,
		"rate_cents": 3_500 * 100,        # $3,500 (vault demo rate)
		"gate_confs": 3,
		"gate_text": "EIP-658 status == 0x1 AND confs >= 3",
		"binding": "static address + running-total match in the lock window (weakest: any unclaimed deposit that covers an open invoice settles it, whatever it was sent for)",
		"binding_category": NOT_UNCONDITIONAL,
		"maturity": "works",
		"maturity_note": "real Sepolia reads + real payer"
							" (bundled wallet signs & broadcasts)",
		"live_url": "https://ethereum-rpc.publicnode.com",
		"testnet_url": "https://ethereum-sepolia-rpc.publicnode.com",
		"testnet_name": "Sepolia",
		# ERC-681 chain ids (vault ETH note): mainnet @1, Sepolia
		# @11155111 - the charge-time mode picks which one the QR wears.
		"chain_id": 1, "testnet_chain_id": 11155111,
		"real_transport": "eth_subscribe WS (latency) + eth_getLogs polling (truth)",
		"sim_block_seconds": 10, "real_block_time": "~12 s",
	},
	"usdc-eth": {
		"label": "Ethereum / USDC",
		"chain": "Ethereum", "asset": "USDC",
		"family": "evm-erc20",
		"unit_name": "micro-USDC",
		"display_decimals": 6, "native_decimals": 6,
		"rate_cents": 100,                # $1.00 - it's a dollar stablecoin
		"gate_confs": 3,
		"gate_text": "EIP-658 status == 0x1 AND confs >= 3",
		"binding": "static address + running-total match (token watcher reads Transfer logs; any covering transfer settles)",
		"binding_category": NOT_UNCONDITIONAL,
		"maturity": "works",
		"maturity_note": "real Sepolia reads + real payer"
							" (bundled wallet signs & broadcasts)",
		"token_contract": USDC_ON_ETHEREUM,
		"testnet_token_contract": USDC_ON_SEPOLIA,
		"live_url": "https://ethereum-rpc.publicnode.com",
		"testnet_url": "https://ethereum-sepolia-rpc.publicnode.com",
		"testnet_name": "Sepolia",
		"chain_id": 1, "testnet_chain_id": 11155111,
		"real_transport": "eth_subscribe('logs') WS + eth_getLogs backfill",
		"sim_block_seconds": 10, "real_block_time": "~12 s",
	},
	"pol": {
		"label": "Polygon / POL",
		"chain": "Polygon", "asset": "POL",
		"family": "evm-native",
		"unit_name": "wei",
		"display_decimals": 6, "native_decimals": 18,
		"rate_cents": 55,                 # $0.55 (vault demo rate)
		"gate_confs": None,               # Polygon does NOT count confs:
		"gate_text": "tx block <= the 'finalized' block tag (Heimdall v2 - supersedes conf counting)",
		"binding": "static address + running-total match in the lock window (any covering deposit settles)",
		"binding_category": NOT_UNCONDITIONAL,
		"maturity": "works",
		"maturity_note": "real Amoy reads + real payer"
							" (bundled wallet signs & broadcasts)",
		"live_url": "https://polygon-bor-rpc.publicnode.com",
		"testnet_url": "https://polygon-amoy-bor-rpc.publicnode.com",
		"testnet_name": "Amoy",
		# vault Polygon note: mainnet @137, Amoy @80002
		"chain_id": 137, "testnet_chain_id": 80002,
		"real_transport": "same EVM machinery as Ethereum",
		# ~2 s is Polygon MAINNET. Amoy, the testnet this build actually
		# reads, was measured at ~1 s - which is why the block walker's
		# per-poll bound (watchers.MAX_BLOCKS_WALKED_PER_POLL - the
		# per-block one, not the wider eth_getLogs window) has to be
		# generous enough to outrun it.
		"sim_block_seconds": 4, "real_block_time": "~2 s (Amoy: ~1 s)",
	},
	"usdc-pol": {
		"label": "Polygon / USDC",
		"chain": "Polygon", "asset": "USDC",
		"family": "evm-erc20",
		"unit_name": "micro-USDC",
		"display_decimals": 6, "native_decimals": 6,
		"rate_cents": 100,
		"gate_confs": None,
		"gate_text": "tx block <= 'finalized' tag (never conf counting on Polygon)",
		"binding": "static address + running-total match (Transfer logs; any covering transfer settles)",
		"binding_category": NOT_UNCONDITIONAL,
		"maturity": "works",
		"maturity_note": "real Amoy reads + real payer"
							" (bundled wallet signs & broadcasts)",
		"token_contract": USDC_ON_POLYGON,
		"testnet_token_contract": USDC_ON_AMOY,
		"live_url": "https://polygon-bor-rpc.publicnode.com",
		"testnet_url": "https://polygon-amoy-bor-rpc.publicnode.com",
		"testnet_name": "Amoy",
		"chain_id": 137, "testnet_chain_id": 80002,
		"real_transport": "same EVM machinery as Ethereum",
		"sim_block_seconds": 4, "real_block_time": "~2 s (Amoy: ~1 s)",
	},
	"sol": {
		"label": "Solana / SOL",
		"chain": "Solana", "asset": "SOL",
		"family": "solana",
		"unit_name": "lamport",
		"display_decimals": 6, "native_decimals": 9,
		"rate_cents": 150 * 100,          # $150 (vault demo rate)
		"gate_confs": None,
		"gate_text": "commitment == 'finalized' (processed books NOTHING, confirmed is display-only)",
		"binding": "static address + fresh 32-byte reference key per sale (Solana Pay)",
		"binding_category": UNCONDITIONAL_PER_SALE,
		"maturity": "works",
		"maturity_note": "real devnet reads + real payer"
							" (bundled wallet signs & broadcasts)",
		"live_url": "https://solana-rpc.publicnode.com",
		"testnet_url": "https://api.devnet.solana.com",
		"testnet_name": "devnet",
		"real_transport": "accountSubscribe WS (latency) + getSignaturesForAddress polling (truth)",
		"sim_block_seconds": 2, "real_block_time": "~400 ms (slot)",
	},
	"usdc-sol": {
		"label": "Solana / USDC",
		"chain": "Solana", "asset": "USDC",
		"family": "solana",
		"unit_name": "micro-USDC",
		"display_decimals": 6, "native_decimals": 6,
		"rate_cents": 100,
		"gate_confs": None,
		"gate_text": "commitment == 'finalized'",
		"binding": "static address + fresh reference key; amount read from token balance deltas",
		# NOT unconditional, and the neighbouring prose is why. D33 established
		# that crediting a recipient's balance delta because a reference appears
		# in the account list is "a race deciding which sale steals the money,
		# not attribution" -- one transfer naming two references settled two
		# sales. `sol` earned its claim by decoding the transfer instruction;
		# this rail has no adapter at all, so the reference is an intention and
		# the amount still comes from a delta. Claiming per-sale here would tell
		# an operator a payment is bound when the described mechanism cannot
		# bind it, and `declared_binding_category` hands this value to any
		# future plugin that does not declare its own.
		"binding_category": NOT_UNCONDITIONAL,
		"maturity": "works",
		"maturity_note": "real devnet reads + real payer"
							" (bundled wallet signs & broadcasts)",
		"token_mint": USDC_MINT_SOLANA,
		"testnet_token_mint": USDC_MINT_DEVNET,
		"live_url": "https://solana-rpc.publicnode.com",
		"testnet_url": "https://api.devnet.solana.com",
		"testnet_name": "devnet",
		"real_transport": "same as SOL; SPL token program moves the USDC",
		"sim_block_seconds": 2, "real_block_time": "~400 ms (slot)",
	},
	"xmr": {
		"label": "Monero / XMR",
		"chain": "Monero", "asset": "XMR",
		"family": "monero",
		"unit_name": "piconero",
		"display_decimals": 6, "native_decimals": 12,
		"rate_cents": 165 * 100,          # $165 (vault demo rate)
		"gate_confs": 10,
		"gate_text": "locked == false AND confs >= 10 AND !double_spend_seen (the locked FLAG rules, not the count)",
		"binding": "fresh subaddress per sale (view-only wallet-rpc create_address)",
		# Monero's mechanism is sound -- wallet-rpc reports an incoming
		# transfer's amount with its `subaddr_index`, which is attribution and
		# not a balance delta. But "fresh" is adapter behaviour, and there is no
		# adapter: `uri.py` emits the recipient the operator configured, and
		# nothing in this package can create a subaddress. Two sales would be
		# shown one address. The claim becomes true in the plugin that allocates
		# per sale, declared there where the code that makes it true lives.
		"binding_category": NOT_UNCONDITIONAL,
		# THE ONE RAIL WHOSE MATURITY IS NOT A CONSTANT. Every other rail
		# either has a public endpoint or does not, and the answer is the
		# same on every terminal. Monero cannot be watched from a public
		# explorer at all - that is the point of Monero - so the rail is
		# DEMO-only on a terminal with no sidecar and REAL on a terminal
		# with one. `maturity_now(rail, mode)` answers per terminal;
		# `maturity` here is the floor, i.e. what an unconfigured build is.
		# See `sidecar_configured` below.
		"maturity": "sim-always",
		"maturity_note": "DEMO-only until a view-only monero-wallet-rpc"
							" sidecar is configured; then real stagenet reads."
							" Monero cannot be watched from a public explorer"
							" by construction, so this rail is local-first.",
		"sidecar": "monero-wallet-rpc",
		# Measured 2026-07-27 against a real sidecar (see harnesses/
		# live_xmr.py): a view-only wallet answers `get_height`,
		# `create_address` and `get_transfers`, and REFUSES
		# `query_key(spend_key)` with -29 "The wallet is watch-only."
		"sidecar_command": "monero-wallet-rpc --stagenet --daemon-address"
							" <remote>:38089 --untrusted-daemon --wallet-dir"
							" <dir> --rpc-bind-port 18083 --disable-rpc-login",
		"live_url": None,
		"testnet_url": None, "testnet_name": "stagenet",
		"real_transport": "your own view-only monero-wallet-rpc (local; get_transfers pool:true)",
		"sim_block_seconds": 12, "real_block_time": "~2 min",
	},
	"xtm": {
		"label": "Tari L1 / XTM",
		"chain": "Tari L1 (Minotari)", "asset": "XTM",
		"family": "tari",
		"unit_name": "MicroTari",
		"display_decimals": 2, "native_decimals": 6,
		"rate_cents": 2,                  # $0.02 (vault demo rate)
		"gate_confs": None,
		"gate_text": "status MINED_CONFIRMED AND INBOUND AND !cancelled (status-keyed; no conf count on the wire)",
		"binding": "payment_id embedded in the TariAddress (RFC-0155)",
		# A payment_id binds money to the id; it does not bind the id to a sale.
		# It is optional, arbitrary and not required to be unique, and this
		# package never allocates one: `uri.py` passes the configured
		# TariAddress through unchanged, so every sale would carry the same id
		# and the winner would be decided by polling order. Same shape as D33.
		"binding_category": NOT_UNCONDITIONAL,
		"maturity": "sim-always",
		# PARKED, explicitly, by A2 (decided 2026-07-25) - not "not yet
		# reached". Three independent blockers, any one of which is enough:
		# the node speaks gRPC (protobuf over HTTP/2) and the stdlib cannot;
		# the explorer publishes no API at all, so self-hosting would need a
		# SECOND sidecar just to translate; and the asset has no listed
		# price on any feed this build can read, so `rates.py` could not
		# quote an invoice even if the watch path existed. The third is the
		# one that makes this a park rather than a backlog item: a rail
		# whose invoice cannot be priced cannot be charged honestly at all.
		"maturity_note": "DEMO-only (simulated) and PARKED, not planned:"
							" gRPC-only transport the stdlib cannot speak, no"
							" public explorer API, and no listed price on any"
							" readable feed - so an invoice could not be quoted"
							" even with a watch path.",
		"live_url": None,
		"testnet_url": None, "testnet_name": "esmeralda",
		"real_transport": "wallet/base-node gRPC (protobuf over HTTP/2)",
		"sim_block_seconds": 12, "real_block_time": "~2 min",
	},
	# -----------------------------------------------------------------
	# THE SAME ASSET, ONE LAYER UP - and the layer is what makes it
	# reachable. Added 2026-08-15 on the maintainer's decision that the Ootle
	# policy layer should only be usable when the sale itself was paid
	# in Ootle, "for sake of simple asset management before
	# interoperability can be used".
	#
	# THE ROW ABOVE IS WHY THIS ONE EXISTS. `xtm` is Tari at layer one
	# and it is parked forever for this terminal: gRPC-only transport,
	# which a stdlib-only Python build cannot speak at all. Ootle is
	# the same money at layer two and its indexer is **HTTPS + JSON** -
	# exactly what `ootle_read.py` has been reading since 2026-08-04
	# with `urllib`. So the rail that could not be built at L1 builds
	# itself at L2, out of a module that already existed.
	#
	# MEASURED BEFORE IT WAS WRITTEN, and the finding that decided the
	# design: an XTR balance in an account vault is a `Stealth`
	# container, whose whole definition is `address`, `revealed_amount`
	# and `locked_amount` (`tari_engine_types-0.37.0/src/
	# resource_container.rs:67`). There are no commitments in it - that
	# is the `Confidential` variant, which this asset does not use for
	# account vaults - so the balance is fully readable by anyone with
	# no key and no fee. Proven 2026-08-15 by reading a 1 uT transfer
	# back off the indexer as `revealed_amount: 1`.
	#
	# THAT IS THE WHOLE WATCH PATH, and it is why this rail is real
	# where `xtm` is not. What it is NOT is a price: see `price_asset`.
	# -----------------------------------------------------------------
	"xtr": {
		"label": "Ootle / XTR",
		"chain": "Tari Ootle", "asset": "XTR",
		"family": "ootle",
		"unit_name": "microTari",
		"display_decimals": 2, "native_decimals": 6,
		"rate_cents": 5,                  # $0.05 *picked* - demo only, no feed
		# PRICED AS TARI, BECAUSE IT IS TARI. XTR on Ootle and XTM on
		# layer one are one asset across two layers, bridged by
		# burn-and-claim (`ClaimBurn` takes a `MinotariBurnClaimProof`).
		# A separate "XTR" row in the feed tables would be a row that
		# can never fill, because an exchange lists the asset and not
		# the layer. So this rail asks for XTM's price, and the day two
		# feeds list it this rail becomes chargeable with no edit here -
		# `rates.unpriced_assets()` is derived from the tables rather
		# than written down as a verdict, which is the whole point of
		# encoding the inequality instead of today's conclusion.
		"price_asset": "XTM",
		# NO CONFIRMATION DEPTH, and this is a property of the consensus
		# rather than a gap. Ootle finality is BFT: a transaction is
		# committed or it is not, there is no reorg to outlive, and
		# `harnesses/measurements/2026-07-31-ootle-finality-3.json`
		# measured the cycle at 58.7 s with a free-phase median of
		# 29.2 s. Waiting "3 more of something" would be waiting for a
		# thing that does not happen here.
		"gate_confs": None,
		"gate_text": "with a payment component: deposits naming THIS sale's"
					" reference total at least the invoiced amount. Without one:"
					" unclaimed deposits into the recipient's XTR vault do."
					" Committed transactions only, and Ootle commits are final -"
					" no confirmation depth",
		# TWO BINDINGS, AND THE ROW DECIDES WHICH. Until 2026-08-31 this
		# said only the first, and added that a component taking a sale
		# reference "would bind exactly and is a new contract". It is no
		# longer new: it was written, attacked, deployed to esmeralda and
		# paid by a stranger's own key the same day (D49-D55). Leaving the
		# old sentence would understate a guarantee the operator already
		# has, which is the quiet direction of D48's defect and the one
		# nobody gets suspicious about.
		#
		# Said at the ADAPTER's level, not the deployment's: whether this
		# rail binds per sale depends on whether the row names a component,
		# and no static table can know that (D45). `catalog.binding_label`
		# answers it from configuration.
		"binding": "per-sale when the rail names a payment component - the"
					" payer passes the sale reference to the component's `pay`"
					" method, so the money itself says which sale it settles."
					" Without one it falls back to a static account and a"
					" running-total match in the lock window: the eth pattern,"
					" and the weakest",
		"binding_category": NOT_UNCONDITIONAL,
		"maturity": "partial",
		"maturity_note": "real esmeralda reads over the same HTTPS+JSON"
							" indexer the policy layer already uses; no in-app"
							" payer, so an outside wallet plays the customer."
							" NOT CHARGEABLE IN A REAL MODE YET: Tari is listed"
							" on none of the five measured feeds, so no price"
							" can be agreed - the watch path is built and the"
							" quote is what is missing.",
		# AND NO SIMULATOR, WHICH IS THE ONE RAIL THAT DOES NOT HAVE ONE.
		# `simulator.py` teaches eight real node dialects; Ootle's real
		# transport is a REST indexer with five documented GETs, and a
		# ninth personality would be a dialect this build INVENTED rather
		# than one it learned. That is the opposite of what that file is
		# for. So demo is refused with a stated reason instead of being
		# answered by fiction - which also keeps the charge button from
		# promising a mode it cannot honour.
		"no_simulator": True,
		# THE MAINNET ROW IS EMPTY ON PURPOSE and it is the same absence
		# `ootle_net.py` records: Tari has published no mainnet indexer.
		# A plausible URL here would turn "not launched" into "not
		# answering".
		"live_url": None,
		"testnet_url": "https://ootle-indexer-a.tari.com",
		"testnet_transport": "ootle-indexer-rest",
		"testnet_name": "esmeralda",
		"real_transport": "Ootle indexer REST (HTTPS + JSON)",
		# The consensus cycle, measured, standing where a block time
		# stands for a chain that has blocks.
		"sim_block_seconds": 10, "real_block_time": "~59 s cycle (measured)",
	},
	"dash": {
		"label": "Dash / DASH",
		"chain": "Dash", "asset": "DASH",
		"family": "bitcoin",              # inherits Bitcoin's shape...
		"dash_chainlocks": True,          # ...plus this
		"unit_name": "duff",
		"display_decimals": 8, "native_decimals": 8,
		"rate_cents": 25 * 100,           # $25 *picked* - vault: "not in demo build"
		"gate_confs": 6,
		# THE GATE LOST ITS FAST HALF ON THE REAL TRANSPORT, and the reason
		# is worth stating where the promise is made. Dash's whole pitch is
		# ChainLocks: one block, then a quorum signature makes reorgs
		# impossible, so a merchant settles in ~2.5 minutes instead of 15.
		# `gettransaction` on Dash Core carries `chainlocked` as a boolean.
		# INSIGHT DOES NOT CARRY IT AT ALL (measured 2026-07-27: a tx
		# answers with `txlock` and `confirmations`, and no chainlock field
		# on the tx OR the block). So on this transport the gate degrades
		# to the slow half it was written as a fallback.
		#
		# And note WHICH field Insight does carry: `txlock` is the
		# InstantSend signal - the exact one the vault's Dash note says is
		# display-only and must NEVER book income. The transport can see
		# the signal we are forbidden to use and cannot see the one we
		# would settle on. That asymmetry is why the lock below is 1200s.
		"gate_text": "confs >= 6 (ChainLock would settle in ~1 block, but the"
						" Insight transport carries no chainlock field, so this"
						" build cannot use it; txlock/islock is display-only and"
						" never books)",
		"binding": "fresh HD address per sale (same pattern as Bitcoin)",
		"binding_category": NOT_UNCONDITIONAL,
		"maturity": "partial",
		"maturity_note": "real testnet reads via Insight REST + fresh HD"
							" addresses; no in-app payer - the faucet plays the"
							" customer. ChainLocks are invisible on this"
							" transport, so the gate is the 6-conf fallback.",
		# live_url stays None and that is a DECISION, not a measurement -
		# the distinction matters because everywhere else in this table None
		# means "nothing answers". `https://insight.dash.org/insight-api`
		# answers fine (measured 2026-07-27: tip 2,511,572, 125s behind wall
		# clock, archive to depth 500,000). Mainnet is a NON-WORKING MODE by
		# the maintainer's decision of 2026-07-26, so the URL is recorded in the
		# comment rather than the field: writing it in would open mainnet as
		# a side effect of a testnet landing.
		"live_url": None,
		"testnet_url": "https://insight.testnet.networks.dash.org/insight-api",
		"testnet_transport": "insight-rest",
		"testnet_name": "testnet",
		"real_transport": "Dash Core RPC + ZMQ (rawtxlock/rawchainlock)",
		"sim_block_seconds": 10, "real_block_time": "~2.5 min",
	},
	"zec": {
		"label": "Zcash / ZEC",
		"chain": "Zcash", "asset": "ZEC",
		"family": "zcash",
		"unit_name": "zatoshi",
		"display_decimals": 8, "native_decimals": 8,
		"rate_cents": 40 * 100,           # $40 *picked* - vault: "not in demo build"
		"gate_confs": 10,
		"gate_text": "confs >= 10 (plain PoW count; no status flag or lock)",
		"binding": "fresh transparent address per sale (the address is the binding; ZIP-321 forbids"
					" a memo on a transparent recipient)",
		"binding_category": NOT_UNCONDITIONAL,
		"maturity": "sim-always",
		# ZEC WAS SUPPOSED TO BE PROMOTED ALONGSIDE DASH, AND THE
		# MEASUREMENT REFUTED IT (2026-07-27). Recording that here, next to
		# the rail, because the decision that said "promote" (A2) is a dated
		# record and must not be edited - but a reader arriving at this
		# entry needs to know why the rail did not move.
		#
		# A2 rested on `api.blockchair.com/zcash/stats` answering. It does.
		# But `stats` is the TIP, and a watch path needs an ADDRESS query,
		# which nobody had asked for. Asked for, it returns HTTP 430: "Your
		# IP address is temporary blacklisted due to exceeding usage of API
		# resources." Within two requests. Minutes later the same blacklist
		# swallowed `/stats` too - the endpoint the decision was made on.
		#
		# That is the CoinGecko finding from the rates landing, again: an
		# aggregator that answers a survey is not therefore an endpoint a
		# terminal can poll every few seconds for the length of a sale.
		# Everything else measured the same day: zecblockexplorer.com 403
		# (Cloudflare), zcashexplorer.app 404, api.3xpl.com 403 (token
		# required), api.zcha.in 520 (dead), explorer.testnet.z.cash has no
		# DNS record at all. There is no keyless Zcash address endpoint in
		# reach, on either network.
		#
		# So ZEC's real blocker is the one XTM has: its live transport is
		# gRPC (lightwalletd), which the stdlib cannot speak. The t-address
		# limit A2 worried about is real but was never the binding
		# constraint. Self-hosting Zebra or lightwalletd remains the honest
		# path and is the sovereignty answer, not a promotion.
		"maturity_note": "DEMO-only (simulated). Measured 2026-07-27: no"
							" keyless public address endpoint exists on either"
							" network - blockchair blacklists the IP within two"
							" requests, and every other explorer API is dead,"
							" token-walled or 403. Real transport is"
							" lightwalletd gRPC, only mimed here.",
		"live_url": None,
		"testnet_url": None, "testnet_name": "testnet",
		"real_transport": "lightwalletd gRPC compact-block streaming + LOCAL trial-decryption",
		"sim_block_seconds": 12, "real_block_time": "~75 s",
	},
}


# Financial identity is configuration, not mutable process state. Exposing the
# literal dict directly let any consumer accidentally change decimals, chain
# ids or token contracts for every later sale in the process. Freeze both the
# table and each row while retaining the familiar mapping API.
RAILS = MappingProxyType({key: MappingProxyType(dict(row)) for key, row in RAILS.items()})


# ---------------------------------------------------------------------------


# ===========================================================================
# Rail lookup and per-mode token identity.
# ===========================================================================


def rail_for(rail_key):
	"""The rail table entry for `rail_key`.

	Raises `KeyError` rather than returning None: every caller of this is
	about to price or address money with the answer, and a None that reaches
	that far becomes a sale charged against nothing.
	"""
	return RAILS[rail_key]


def rail_keys():
	"""Every rail key, in table order."""
	return tuple(RAILS)


def token_contract_for(rail, mode):
	"""USDC lives at a different contract on each network - the mainnet
	one only on explicit mainnet, Circle's test deployment otherwise."""
	require_mode(mode)
	if mode != MAINNET:
		return rail["testnet_token_contract"]
	return rail["token_contract"]


def token_mint_for(rail, mode):
	"""Same idea for Solana: mainnet mint vs. the devnet mint."""
	require_mode(mode)
	if mode != MAINNET:
		return rail["testnet_token_mint"]
	return rail["token_mint"]


# ===========================================================================
# Unit math - all integer ("integer base units only - never floats").
# ===========================================================================

# A rate is carried in MICRO-CENTS per whole coin - cents x 10^4, USD x 10^6.
#
# Integer cents is the obvious unit and it is measurably the wrong one. POL
# read $0.07745 on 2026-07-26; in whole cents that is 8, a 3.3% error in the
# UNIT before any feed has disagreed with anything. The demo table never
# showed this because it pegged POL at a tidy 55c, and every cheap asset has
# the same problem waiting in it.
#
# DERIVED, not restated. `rates.MICROCENTS_PER_USD` is the one definition of
# the scale in this package; writing `10_000` again here is how the two drift
# apart by one zero on a day nobody is looking at both files.
MICROCENTS_PER_CENT = MICROCENTS_PER_USD // 100



#: The rail family whose sales may earn loyalty points.
#:
#: **THE MAINTAINER'S DECISION, 2026-08-15:** the Ootle smart-contract tooling is usable
#: only when the sale itself was paid in Ootle — *"for sake of simple asset
#: management before interoperability can be used"*. A constant rather than a
#: literal inside the predicate, because the day interoperability arrives this
#: is the one line that changes.
POLICY_LAYER_FAMILY = "ootle"


def earns_policy_points(rail):
	"""May a sale on this rail earn loyalty points?

	**THE RULE: the money and the points have to be on the same network.**
	Until 2026-08-15 they did not have to be, and the terminal proved it — a
	Solana devnet sale awarded points on Ootle, and the receipt said so
	(`1884e30`). That worked, and it left the merchant holding two assets for
	one transaction and reconciling across a bridge that does not exist yet.
	The maintainer's call is to collapse that until interoperability is real: one asset
	in, one asset out.

	**THIS IS A RESTRICTION AND IT IS MEANT TO BE ONE.** With it in force,
	every rail this terminal can currently charge on stops offering points,
	and the Ootle rail that would offer them cannot be charged in a real mode
	because Tari is listed on no exchange feed. So loyalty is, today, offered
	on nothing. That is the honest consequence of the rule and not a defect in
	it: `rates.unpriced_assets()` is derived from measured feed tables rather
	than written down as a verdict, so the day two feeds list Tari, this rail
	becomes chargeable and loyalty starts working again with no code change.
	The inequality is encoded; the conclusion is not.

	Deliberately NOT a `merchant.json` switch. A switch would make it a thing
	an operator turns off on a busy afternoon, and the reason it exists is an
	asset-management property of the whole programme rather than a preference
	about one till.
	"""
	return rail.get("family") == POLICY_LAYER_FAMILY


def price_asset(rail):
	"""Which asset's price prices this rail. Its own, unless it says otherwise.

	**ADDED 2026-08-15 FOR THE OOTLE RAIL, and it is a distinction the other
	ten rails never needed.** For every one of them the thing being paid and
	the thing being quoted are the same ticker. XTR is Tari at layer two and
	XTM is Tari at layer one — one asset, two layers, bridged by
	burn-and-claim — and **an exchange lists the asset, not the layer**. So a
	separate `XTR` row in the feed tables would be a row that can never fill,
	and asking for one would be building a permanent refusal on a
	misunderstanding.

	The default keeps every existing rail exactly where it was: `rail["asset"]`
	is the answer unless a rail has a reason, and only one rail has a reason.
	"""
	return rail.get("price_asset") or rail["asset"]


def rail_demo_microcents(rail):
	"""The hardcoded demo rate, in the precise unit. RAILS stays in whole
	cents on purpose: it is a demo table, it is not measured, and dressing
	it in six decimal places would make it look like it was."""
	return rail["rate_cents"] * MICROCENTS_PER_CENT


def usd_cents_to_native(rail, usd_cents, rate_microcents=None):
	"""
	Dollars -> the asset's native integer, rounding ONCE at display
	precision, then scaling to native. E.g. $6.25 of ETH at $3,500:
		display units: 625 * 10^6 * 10^4 // 3500000000 = 1785 µETH (rounded HERE)
		native:        1785 µETH * 10^12              = 1785000000000000 wei

	`rate_microcents` is the quote to price against, and passing it is what
	a live sale does: the caller has a rate WITH a source and a time, and
	this function must use that one rather than looking a number up for
	itself. Omitted, it falls back to the rail's demo constant - and
	nothing ever writes a live number back into RAILS. That is deliberate:
	a module-level dict that changes under the app is exactly how a sale
	gets priced at one rate and settled at another with no record of either.
	"""
	if rate_microcents is None:
		rate_microcents = rail_demo_microcents(rail)
	normalized_rate = _coerce_integer(rate_microcents)
	if normalized_rate is None:
		raise InvalidRate(rate_microcents) from None
	rate_microcents = normalized_rate
	if rate_microcents <= 0:
		raise InvalidRate(rate_microcents)
	normalized_cents = _coerce_integer(usd_cents)
	if normalized_cents is None:
		raise InvalidAmount("usd_cents", usd_cents) from None
	usd_cents = normalized_cents
	if usd_cents <= 0:
		raise InvalidAmount("usd_cents", usd_cents)
	display_units = (usd_cents * 10**rail["display_decimals"]
						* MICROCENTS_PER_CENT) // rate_microcents
	return display_units * 10**(rail["native_decimals"] - rail["display_decimals"])


def native_to_usd_cents(rail, native_units, rate_microcents=None):
	"""
	The inverse, same two-step (native -> display units -> cents) so no
	float ever appears. Lives here beside its forward twin rather than in
	pos_actions, because they must round the same way or a sale that paid
	exactly reads as a cent short - see h_B2, which measures precisely
	that. Same `rate_microcents` contract as above.
	"""
	if rate_microcents is None:
		rate_microcents = rail_demo_microcents(rail)
	normalized_rate = _coerce_integer(rate_microcents)
	if normalized_rate is None or normalized_rate <= 0:
		raise InvalidRate(rate_microcents)
	normalized_units = _coerce_integer(native_units)
	if normalized_units is None or normalized_units < 0:
		raise InvalidAmount("native_units", native_units, minimum=0)
	rate_microcents = normalized_rate
	native_units = normalized_units
	display_units = native_units // 10**(rail["native_decimals"]
											- rail["display_decimals"])
	return (display_units * rate_microcents
			// (10**rail["display_decimals"] * MICROCENTS_PER_CENT))


def format_amount(rail, native_units):
	"""Native integer -> human string, e.g. 1785000000000000 -> '0.001785'."""
	normalized_units = _coerce_integer(native_units)
	if normalized_units is None or normalized_units < 0:
		raise InvalidAmount("native_units", native_units, minimum=0)
	native_units = normalized_units
	display_units = native_units // 10**(rail["native_decimals"] - rail["display_decimals"])
	whole, frac = divmod(display_units, 10**rail["display_decimals"])
	return f"{whole}.{frac:0{rail['display_decimals']}d}"


# ===========================================================================
# Exact representability - the invariant that keeps a QR and a sale agreeing.
# ===========================================================================


def representable_amount(rail, native_units):
	"""The largest amount <= `native_units` that `format_amount` writes exactly.

	Rails whose two decimal counts match return the amount unchanged; there is
	nothing to truncate. Rails with room between them (ETH, SOL, XMR, ...)
	round DOWN to the display grid.
	"""
	normalized_units = _coerce_integer(native_units)
	if normalized_units is None or normalized_units < 0:
		raise InvalidAmount("native_units", native_units, minimum=0)
	step = 10 ** (rail["native_decimals"] - rail["display_decimals"])
	return (normalized_units // step) * step


def is_exactly_displayable(rail, native_units):
	"""Does `format_amount` write `native_units` without losing anything?

	**This is the invariant behind every decimal-amount URI.** BIP-21, Solana
	Pay and ZIP-321 carry the DISPLAY form, so if the display form is a
	truncation of the invoice, the customer pays what the QR says, the sale
	stays short of its own invoice, and no amount of waiting fixes it.

	`rails.usd_cents_to_native` satisfies this by construction -- it rounds at
	display precision before scaling up. `rates.native_for` does NOT: it
	divides straight to native precision, so on any rail where the decimals
	differ it can produce an amount no URI can state.
	"""
	normalized_units = _coerce_integer(native_units)
	if normalized_units is None or normalized_units < 0:
		raise InvalidAmount("native_units", native_units, minimum=0)
	return normalized_units == representable_amount(rail, normalized_units)


def invoice_amount(rail, usd_cents, rate_microcents):
	"""The amount to invoice: exact, and always statable in a URI.

	**Use this on a charge path.** The guarantee it carries is the one that
	matters at a counter: whatever comes back can be written exactly by
	`format_amount`, so the QR asks for precisely what the sale expects.

	**`rate_microcents` is REQUIRED here and optional on
	`usd_cents_to_native`, which is the whole difference between them.** The
	optional form falls back to this rail's hardcoded demo constant, and a
	charge path that reached that fallback by forgetting an argument would
	price real money at a number nobody quoted -- the same hazard
	`rates.quote` refuses on mainnet, arriving through a different door.
	Pricing a sale from the demo table should be an act you can see in the
	call, so pass `rail_demo_microcents(rail)` and mean it.
	"""
	if rate_microcents is None:
		raise InvalidRate(rate_microcents)
	amount = usd_cents_to_native(rail, usd_cents, rate_microcents)
	if amount <= 0:
		raise InvalidAmount("native_units", amount)
	return amount
