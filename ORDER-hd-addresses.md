# ORDER — a fresh receiving address per sale, derived from an operator xpub

STATUS: OPEN
OWNER: one builder. Nothing else may edit these paths while it is open.

## WHY

`cryptopos/DECISIONS.md` D5 records why `btc` is seeded switched **off**: the
`bitcoin:testnet4` adapter refuses `capture_baseline` on a recipient with any
transaction history, this terminal has no per-sale address source, and the
proposal to let it accept a reused address lost to seven concrete failure
sequences. Read D5 before starting — the first sequence needs no misuse at all
and is the reason this work exists.

The same document records that the EVM adapters do *not* refuse reuse and are
exposed to the same sequences. So this is not a Bitcoin feature. It is the
missing half of attribution on every rail, and BTC is simply the rail whose
adapter was honest enough to refuse without it.

## OWNS — the complete list of paths this order may create or modify
```
packages/cryptopos-core/src/cryptopos_core/hd.py
packages/cryptopos-core/tests/test_hd.py
PROOF.md
```

## READS — may be opened, must be left byte-identical
```
packages/cryptopos-core/src/cryptopos_core/addresses.py
packages/cryptopos-core/src/cryptopos_core/bitcoin.py
packages/cryptopos-core/src/cryptopos_core/errors.py
packages/cryptopos-core/src/cryptopos_core/plugin.py
DECISIONS.md
Makefile
```

**Nothing in `cryptopos/` (the Frappe app half) is touched by this order.**
Wiring the app to this module is a separate order, because it needs a doctype
field and a migration and this one does not.

## SURFACE — the whole of it, and nothing may be added

```python
class InvalidExtendedKey(CryptoPosError): ...   # goes in errors.py? NO -- see below

def parse_extended_key(text: str) -> ExtendedKey: ...
def derive_child(key: ExtendedKey, index: int) -> ExtendedKey: ...
def derive_path(key: ExtendedKey, path: str) -> ExtendedKey: ...
def p2wpkh_address(key: ExtendedKey, hrp: str) -> str: ...

@dataclass(frozen=True)
class ExtendedKey:
	version: int
	depth: int
	fingerprint: bytes
	child_number: int
	chain_code: bytes
	public_key: bytes      # 33-byte compressed SEC1
```

`errors.py` is in READS and must stay byte-identical, so the new refusal type
lives in `hd.py` and subclasses `CryptoPosError` imported from there. If you
believe it belongs in `errors.py`, stop and write the question rather than
editing a READS file.

## WHAT IT MUST DO

**Public keys only. There is no private key path in this module, ever.** The
terminal is watch-only; a module that can derive a spending key is a module
that can be made to leak one. `parse_extended_key` must refuse an `xprv`/`tprv`
outright, by version bytes, with a message saying why.

1. **`parse_extended_key`** — base58check-decode a `tpub`/`xpub`/`vpub`/`zpub`,
   verify the checksum, and refuse: a bad checksum, a wrong length, a private
   version, and a public key that is not a valid compressed point.
2. **`derive_child`** — BIP-32 CKDpub. Hardened indices (>= 2**31) must be
   refused: they are underivable from a public key, and refusing is the whole
   point. Needs secp256k1 point addition; pure Python, standard library only.
   The `IL >= n` and `point at infinity` cases must raise rather than return a
   wrong key, even though they are astronomically rare — say so in a comment.
3. **`derive_path`** — accept `"0/17"` style relative paths only. A leading
   `m/` refers to a master key this module cannot have; refuse it.
4. **`p2wpkh_address`** — BIP-84 witness-v0: `hash160(pubkey)` into a bech32
   address with the given `hrp` (`tb` for testnet, `bc` for mainnet).
   `addresses.py` already has bech32 *decode*; encode does not exist and must
   be written here.

## PROOF — this is the part that decides whether it lands

`make check` runs `prove` and `worth` over this package and both are binding.
Beyond that, this module is cryptography and is tested with **published test
vectors, not with values this implementation produced**:

- **BIP-32 test vectors 1, 2 and 3**, public-derivation chains only.
- **BIP-84 §"Test vectors"** — the `zpub` and its first two receiving
  addresses. Derive them and compare against the published strings.
- **BIP-173/BIP-350 bech32 vectors** for the encoder, including the invalid
  ones, which must be refused.
- A **round trip against the existing decoder**: every address
  `p2wpkh_address` emits must satisfy `addresses.validate("btc", address,
  "testnet")` with verdict `ok`. This is what ties the new code to the code
  that already refuses bad addresses.

Paste the vectors into the test file with a comment naming which BIP each came
from. A vector without a citation is a number somebody made up.

## INVARIANTS

- Standard library only. No new dependency, in this package, ever.
- No private-key derivation, no signing, no `xprv` acceptance.
- `make check` green: lint, the suite on 3.9/3.11/3.13/3.14, the wheel, prove,
  the terminal suites, and worth.
- `PROOF.md` gains a row for every new symbol — `prove` fails otherwise.

## DONE WHEN
```bash
make lint
make test
make matrix
make prove
make worth
```
Paste the real output of each into the RESULT section below.

## NOT IN THIS SLICE
- Wiring the Frappe app to it, the doctype field for the xpub, the gap-limit
  policy, or re-enabling the `btc` rail → all a separate order.
- Any change to `bitcoin.py`, `evm.py` or the catalog → separate order.
- Deriving addresses for EVM rails → different derivation (BIP-44 + keccak),
  and a separate order.

---
## RESULT — filled in by the builder, not before
