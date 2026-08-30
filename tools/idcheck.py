"""What is this string, and is it whole? Offline, no keys, no network.

    python3 tools/idcheck.py <string> [<string> ...]
    echo "<string>" | python3 tools/idcheck.py

**Why this exists.** Long opaque identifiers are the one thing an agent cannot
produce from memory and the one thing this workspace is made of: addresses,
transaction ids, signatures, genesis hashes, component ids. A wrong one does not
look wrong. It looks exactly like a right one, passes every offline test, and
fails at the chain — after the money has moved, or worse, without ever failing at
all because nothing compared it to anything.

Three real ones from 2026-08-25, all of which this tool answers in one call:

  * `EtWTRABZaYq6iMfeYKouRu166VU2xqa1` — the Solana devnet genesis hash with its
    last twelve characters missing. Fourteen unit tests passed against it and the
    rail would have refused every real devnet node as not-devnet.
  * `11111111111111111111111111111111` — used as a merchant address in test
    fixtures. It is the System Program. The fixtures were paying the runtime.
  * `"1" * 87` — a made-up Solana signature. Base58 `1` is a leading zero byte,
    so it decodes to 87 zero bytes rather than a 64-byte signature; devnet
    answered `Invalid param: WrongSize`.

So the questions this answers are: what shape is it, how many bytes does it
really carry, is its checksum sound, and is it a well-known constant somebody has
mistaken for their own. It never guesses and it never invents — given something
it cannot place, it says so.

Address checking is delegated to `cryptopos_core.addresses.validate`, asked once
per rail, rather than reimplemented here. A second copy of a checksum is a second
thing that can be wrong on its own.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packages" / "cryptopos-core" / "src"))

from cryptopos_core import rails as _core_rails
from cryptopos_core.addresses import OK, UNCHECKED, validate

B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
HEX = "0123456789abcdefABCDEF"

# Every rail the package knows, READ FROM THE PACKAGE. The first draft of this
# line was the same twelve keys typed out, and they were correct -- which is not
# the same as being right. A hand-kept copy of somebody else's list is a list
# that stops covering the system on the day it grows, and this tool exists
# because of a defect class whose whole shape is "correct when written".
RAILS = tuple(_core_rails.RAILS)

# Strings that are somebody else's constant, not your identifier. Every one of
# these is a valid address of its kind, which is exactly why they get used by
# mistake and why a shape check alone will never object.
WELL_KNOWN = {
    "11111111111111111111111111111111":
        "the Solana System Program. Not a wallet: it can appear as the program "
        "of an instruction and never as a merchant.",
    "0x0000000000000000000000000000000000000000":
        "the EVM zero address. Sending here burns the money.",
    "0x000000000000000000000000000000000000dEaD":
        "the conventional EVM burn address.",
    "resource_" + "01" * 32:
        "the Ootle XTR resource — Tari's own token, not a merchant resource.",
    "So11111111111111111111111111111111111111112":
        "the wrapped-SOL MINT. It is a real on-chain account and it is not a "
        "recipient: a transfer addressed to a mint fails.",
    "ComputeBudget111111111111111111111111111111":
        "the Solana Compute Budget program.",
    "Vote111111111111111111111111111111111111111":
        "the Solana Vote program.",
    "Stake11111111111111111111111111111111111111":
        "the Solana Stake program.",
    "AddressLookupTab1e1111111111111111111111111":
        "the Address Lookup Table program.",
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA":
        "the SPL Token program — the owner of every token account, never one.",
}


def b58_bytes(text):
    """How many bytes this base58 text really carries, or None if not base58."""
    if not text or any(character not in B58_ALPHABET for character in text):
        return None
    value = 0
    for character in text:
        value = value * 58 + B58_ALPHABET.index(character)
    body = (value.bit_length() + 7) // 8
    return body + (len(text) - len(text.lstrip("1")))


def is_hex(text):
    return bool(text) and all(character in HEX for character in text)


def shapes(text):
    """Every shape this string could be, with what is right or wrong about it."""
    found = []

    if text.startswith("0x") and is_hex(text[2:]):
        digits = len(text) - 2
        if digits == 40:
            found.append(("EVM address", f"20 bytes — {digits} hex digits"))
        elif digits == 64:
            found.append(("EVM transaction hash", f"32 bytes — {digits} hex digits"))
        else:
            found.append(("0x-prefixed hex", f"{digits} hex digits — NOT 40 (address) or 64 (tx hash)"))
    elif text.startswith("0x"):
        found.append(("0x-prefixed, and NOT hex",
                      "something that looks like an EVM identifier and cannot be one — "
                      "a harness placeholder writes exactly this shape"))

    if is_hex(text) and not text.startswith("0x"):
        if len(text) == 64:
            found.append(("Bitcoin-style transaction id", "32 bytes — 64 hex digits"))
        else:
            found.append(("bare hex", f"{len(text)} hex digits — a txid is 64"))

    for prefix in ("component_", "resource_", "template_", "account_"):
        if text.startswith(prefix):
            body = text[len(prefix):]
            note = (f"{len(body)} hex digits" if is_hex(body) else "body is NOT hex")
            if is_hex(body) and len(body) != 64:
                note += " — an Ootle address body is 64"
            found.append((f"Ootle {prefix.rstrip('_')} address", note))

    carried = b58_bytes(text)
    if carried is not None:
        if carried == 32:
            found.append(("base58 32-byte key", "a Solana address, or a Solana Pay reference"))
        elif carried == 64:
            found.append(("base58 64-byte value", "a Solana transaction signature"))
        else:
            direction = ("shorter than a 32-byte key — which is what truncation looks like"
                         if carried < 32 else
                         "between a key and a signature" if carried < 64 else
                         "longer than a 64-byte signature")
            found.append(("base58", f"decodes to {carried} bytes — NOT 32 (key) or 64 "
                                    f"(signature), and {direction}"))
    return found


def report(text):
    """Print what is known about `text`. Returns 1 if something is wrong with it."""
    print(f"\n{text}")
    print(f"  length {len(text)}")
    wrong = 0

    # Case-insensitive for EVM: `0x...dEaD` and `0x...dead` are the SAME address,
    # and only one of them was in the table. A constant that a change of case
    # walks past is a constant that is not really being checked.
    known = WELL_KNOWN.get(text) or (
        WELL_KNOWN.get(text.lower()) if text.startswith("0x") else None)
    if known is None and text.startswith("0x"):
        for candidate, why in WELL_KNOWN.items():
            if candidate.startswith("0x") and candidate.lower() == text.lower():
                known = why
                break
    if known:
        print(f"  ** WELL-KNOWN CONSTANT: {known}")
        wrong = 1

    found = shapes(text)
    for name, note in found:
        print(f"  shape: {name} ({note})")
        if "NOT" in note or "truncation" in note or "and NOT hex" in name:
            wrong = 1

    # CHECKED and UNCHECKED are kept apart, and the wording of the second one
    # matters more than the first. Solana, Tari and Ootle addresses carry no
    # local checksum, so their validators return UNCHECKED for anything of
    # roughly the right shape -- including the truncated genesis hash above.
    # Printing that under a heading like "valid address for" would be this
    # tool telling a reader the opposite of the truth about the one string it
    # exists to catch.
    checked = []
    unverifiable = {}
    for rail in RAILS:
        for mode in ("testnet", "mainnet"):
            verdict, reason = validate(rail, text, mode)
            if verdict == OK:
                checked.append(f"{rail}/{mode}")
            elif verdict == UNCHECKED:
                unverifiable.setdefault(reason, set()).add(rail)

    if checked:
        print(f"  CHECKED — checksum holds for: {', '.join(checked)}")

    # UNCHECKED IS THE MOST IMPORTANT ANSWER THIS TOOL CAN GIVE, and the first
    # version discarded it. `validate` distinguishes "no checksum exists on this
    # chain" from "this address is all one case, so its checksum is absent and a
    # typo in it CANNOT be detected" -- and the second is a warning about the
    # string in front of you, not a fact about a protocol. Dropping it made a
    # lowercase EVM address print exactly like a checksummed one.
    for reason, rail_set in sorted(unverifiable.items()):
        if PROTOCOL_HAS_NO_CHECKSUM(reason, rail_set):
            continue
        print(f"  NOT VERIFIED — {reason}")
        wrong = 1

    if not checked and not found:
        print("  no shape recognised and no rail checksum holds — this tool will not")
        print("  guess what it is, and neither should you")
        wrong = 1

    return wrong


# Reasons that are facts about a CHAIN rather than about the string in hand.
# Derived by asking the validator about a control, so the day a chain grows a
# checksum this stops claiming otherwise by itself.
_CONTROL = "zzz definitely not an address zzz"
_BLANKET_REASONS = frozenset(
    validate(rail, _CONTROL, "testnet")[1]
    for rail in RAILS
    if validate(rail, _CONTROL, "testnet")[0] == UNCHECKED
)


def PROTOCOL_HAS_NO_CHECKSUM(reason, _rails):
    """Is this reason a property of the chain, or a warning about this string?"""
    return reason in _BLANKET_REASONS


def rails_that_check_nothing():
    """Rails whose LEGACY validator says UNCHECKED even for plainly bogus text.

    **Legacy, and the word matters.** This asks
    `cryptopos_core.addresses.validate`, which is not what the running terminal
    uses to accept a recipient -- `catalog.plugin_for(rail).validate_recipient`
    is. Those disagree: `validate("xtr", "total nonsense", ...)` returns
    UNCHECKED, while the Ootle adapter REFUSES the same text because it does not
    match `account_`/`component_` plus hex. An earlier footer here read
    "xtr accepts any text", which was a statement about the layer this tool
    happened to query rather than about the terminal.

    So the wording names the layer. A tool that quietly answers a different
    question than the one asked is the defect it was built to catch.
    """
    return tuple(
        rail for rail in RAILS
        if validate(rail, _CONTROL, "testnet")[0] == UNCHECKED
    )


def main(argv):
    values = argv or [line.strip() for line in sys.stdin if line.strip()]
    if not values:
        print(__doc__.strip().splitlines()[2].strip())
        return 2
    problems = 0
    for value in values:
        problems += report(value)

    blind = rails_that_check_nothing()
    if blind:
        print(f"\nnote: the LEGACY validator returns 'unchecked' for {', '.join(blind)} on any")
        print("input — those chains publish no local checksum. The running terminal uses each")
        print("rail adapter's own validate_recipient, which is stricter: the Ootle adapter")
        print("refuses text that does not match account_/component_ plus hex. This tool")
        print("reports the legacy layer, so treat a quiet answer here as 'not looked at'.")
    print()
    # Non-zero when anything was malformed, truncated, unplaceable, or a
    # well-known constant. A checker that always exits 0 cannot gate anything,
    # and "read the prose" is not a gate.
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
