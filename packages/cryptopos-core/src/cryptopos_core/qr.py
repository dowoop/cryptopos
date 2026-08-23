"""QR encoding — the same generator the tkinter terminal uses.

`qrcodegen.py` is vendored unchanged (MIT, Project Nayuki) rather than
swapped for a JavaScript library, so the symbol the customer scans off the
screen is produced by the same encoder that produced it before the port. A QR
that differs between two surfaces of the same terminal is a defect that only
shows up at the counter.

What crosses the wire is the module grid, not markup. A host that sanitises
stored HTML will strip exactly the attributes an SVG needs -- `d` and `fill`
-- leaving a well-formed and completely blank image. Sending the bits and
drawing them at the surface sidesteps a sanitiser that is right to be
suspicious of stored markup, and keeps the encoding next to the vendored
library rather than reimplemented in whatever renders it.
"""

from . import qrcodegen

# The spec requires four modules of quiet zone. Scanners fail intermittently
# without it, and intermittently is the worst way for a payment surface to
# fail: it looks like the customer's phone.
QUIET_ZONE_MODULES = 4


def modules_for(text):
	"""Return {"size", "quiet", "rows"} for `text`.

	`rows` is one string of "0"/"1" per row, excluding the quiet zone -- the
	client adds that from `quiet` so the payload stays small.

	Medium error correction: this is displayed on a screen at the counter,
	not printed on a crumpled receipt, so the redundancy of a higher level
	buys resilience the situation does not need and costs module density the
	scanner does.
	"""
	code = qrcodegen.QrCode.encode_text(text, qrcodegen.QrCode.Ecc.MEDIUM)
	size = code.get_size()
	rows = ["".join("1" if code.get_module(x, y) else "0" for x in range(size)) for y in range(size)]
	return {"size": size, "quiet": QUIET_ZONE_MODULES, "rows": rows}
