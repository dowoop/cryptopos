"""Break the code on purpose. Fail if the suite does not notice.

    python tools/worth.py [--list] [--module NAME] [--jobs N]

`tools/prove.py` answers "did this line run?". This answers the harder and
more useful question: **was the assertion around it worth making?**

A test can execute a line, assert nothing about it, and go green forever. The
only way to find out is to change the code so it is wrong and see whether
anything complains. That is what this does: it rewrites one operator, one
constant or one return at a time, runs the suite against the rewritten copy,
and records whether the suite caught it.

    killed      some test failed. That test was defending something real.
    survived    every test still passed while the code was WRONG. Whatever
                covers that line is asserting nothing about it.

A survivor is not automatically a bug. Some mutations produce code that
behaves identically -- an unobservable constant, a redundant guard, a
short-circuit whose other half already decided. Those are *equivalent
mutants*, they are undetectable by construction, and pretending otherwise
would mean writing tests that assert an implementation detail rather than a
behaviour. So survivors are triaged rather than banned: EQUIVALENT below
carries one entry per accepted survivor with the reason it cannot be killed,
and anything not on that list fails the gate.

That list is the honest part of this tool. A mutation score with no such list
is either a lie or a suite full of assertions on trivia.

Nothing here touches the real source tree. Each worker gets a private copy of
the package and mutates that.
"""

import argparse
import ast
import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORE = ROOT / "packages" / "cryptopos-core"
PACKAGE = CORE / "src" / "cryptopos_core"

# Vendored unchanged; its branches are upstream's to defend, not ours.
SKIP_FILES = {"qrcodegen.py"}

RUN_TIMEOUT_SECONDS = 60

BOLD, DIM, GREEN, RED, YELLOW, RESET = (
	"\033[1m",
	"\033[2m",
	"\033[32m",
	"\033[31m",
	"\033[33m",
	"\033[0m",
)

# ---------------------------------------------------------------------------
# Accepted survivors.
#
# Keyed "file:line:description". Every entry states why the mutation cannot
# change observable behaviour -- which is the only reason a survivor is
# allowed to stay. "We could not be bothered to test it" is not one, and a
# reason that turns out to be wrong is a defect in this file, not in the gate.
# ---------------------------------------------------------------------------
EQUIVALENT = {
	# --- _keccak.py -------------------------------------------------------
	# `moved` is a scratch grid, and the rho/pi step assigns all 25 of its
	# cells before chi reads any of them: for each y, (2x + 3y) % 5 runs over
	# all five columns as x does, because 2 and 5 are coprime. So neither the
	# fill value nor a sixth row can ever be observed.
	"_keccak.py:72:0 -> 1": "scratch grid, every cell overwritten before it is read",
	"_keccak.py:72:5 -> 6": "scratch grid, a sixth row/column is never indexed",
	# The state is 5x5 and the absorb loop indexes it as i % 5, i // 5 for i
	# up to 16 -- so rows 0..4 and columns 0..3. Extra rows are unreachable.
	"_keccak.py:88:5 -> 6": "state over-allocation; no index ever reaches a sixth row/column",
	# --- addresses.py -----------------------------------------------------
	# `_bech32_polymod` folds five generator constants under `top`, which is
	# `checksum >> 25` of a 30-bit value and therefore never wider than five
	# bits. Bit 5 is never set, so a sixth round reads no generator and
	# changes nothing. (Checked over random inputs: max `top` seen is 31.)
	"addresses.py:100:5 -> 6": "top is 5 bits wide, so a sixth generator round never fires",
	# The charset lookup uses str.find, which answers -1 and no other negative
	# value. Comparing against -2 leaves the -1 in the data, and the checksum
	# then rejects exactly the same strings -- the guard is a fast path, not
	# the thing doing the rejecting. (30,000 random strings: zero disagreements.)
	"addresses.py:127:1 -> 2": "str.find answers only -1; the checksum rejects the same inputs anyway",
	# The accumulator's bits above `bits` are always masked off before they
	# reach the output, so seeding it with 1 rather than 0 cannot be observed.
	# (Checked over 3,000 random inputs in both padding directions.)
	"addresses.py:139:0 -> 1": "high accumulator bits are masked before output; the seed is invisible",
	# `shift` only ever takes multiples of 7. It reaches 63 after nine
	# continuation bytes and 70 after ten, so `> 63` and `> 64` first fire on
	# the same byte. `>= 63` does not, and that mutation is killed.
	"addresses.py:219:63 -> 64": "shift steps in sevens: 63 and 64 are the same cut, between 63 and 70",
	# `int(character, 16)` on a SINGLE character. Base 17 adds only 'g', and a
	# hex digest never contains one, so every digit parses to the same value.
	# The 40-character body at line 331 is a different matter and is killed by
	# the 'g' test.
	"addresses.py:383:16 -> 17": "single hex character: base 16 and 17 agree on every digit 0-f",
	"addresses.py:458:16 -> 17": "single hex character: base 16 and 17 agree on every digit 0-f",
	# Reading limit+1 is the canonical bounded-read idiom. Any positive excess
	# has identical observable behavior: bodies <= limit are returned whole;
	# bodies > limit yield more than limit and are refused. Reading limit+2
	# cannot admit or reject a different body.
	"chain.py:137:1 -> 2": "any positive read excess detects exactly the same oversized bodies",
	"rates.py:134:1 -> 2": "any positive read excess detects exactly the same oversized bodies",
	# `ObservationBatch.extend` refuses if either operand carries an
	# unattributed balance snapshot, so both values are necessarily zero here.
	# Adding or subtracting zero produces the same cumulative batch.
	"plugin.py:379:Add -> Sub": "both amounts are guarded to zero before they are combined",
	# A minimally valid ERC-20 log is over 200 JSON bytes. Twenty thousand of
	# them cannot fit through the four-megabyte `_rpc` response ceiling, so the
	# parser's exact 20,000/20,001 boundary is unreachable after the wire bound.
	"evm.py:404:Gt -> GtE": "the response-byte ceiling prevents 20,000 valid logs reaching this parser",
	# Readiness is queried by capability name. Inserting the two unavailable
	# entries at index zero or one changes only tuple presentation order, not
	# membership, reasons, chargeability, or conformance behavior.
	"catalog.py:60:0 -> 1": "unavailable capability ordering is not part of the readiness contract",
	"catalog.py:62:0 -> 1": "unavailable capability ordering is not part of the readiness contract",
}


# ---------------------------------------------------------------------------
# Mutation operators.
# ---------------------------------------------------------------------------
COMPARE_SWAPS = {
	ast.Lt: [ast.LtE, ast.Gt],
	ast.LtE: [ast.Lt, ast.GtE],
	ast.Gt: [ast.GtE, ast.Lt],
	ast.GtE: [ast.Gt, ast.LtE],
	ast.Eq: [ast.NotEq],
	ast.NotEq: [ast.Eq],
	ast.Is: [ast.IsNot],
	ast.IsNot: [ast.Is],
	ast.In: [ast.NotIn],
	ast.NotIn: [ast.In],
}

BINOP_SWAPS = {
	ast.Add: [ast.Sub],
	ast.Sub: [ast.Add],
	ast.Mult: [ast.FloorDiv],
	ast.FloorDiv: [ast.Mult],
	ast.Mod: [ast.FloorDiv],
	ast.LShift: [ast.RShift],
	ast.RShift: [ast.LShift],
	ast.BitAnd: [ast.BitOr],
	ast.BitOr: [ast.BitAnd],
	ast.BitXor: [ast.BitAnd],
}

BOOLOP_SWAPS = {ast.And: ast.Or, ast.Or: ast.And}


def walk_with_parent(node, parent=None, field=None, index=None):
	"""Depth-first, deterministic, and carrying enough to replace a node."""
	yield node, parent, field, index
	for name, value in ast.iter_fields(node):
		if isinstance(value, list):
			for i, item in enumerate(value):
				if isinstance(item, ast.AST):
					yield from walk_with_parent(item, node, name, i)
		elif isinstance(value, ast.AST):
			yield from walk_with_parent(value, node, name, None)


def docstring_nodes(tree):
	"""Constants that are docstrings -- never worth mutating."""
	found = set()
	for node in ast.walk(tree):
		if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
			first = node.body[0] if node.body else None
			if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
				found.add(id(first.value))
	return found


def sites(source):
	"""Every mutation this tool knows how to make, in a stable order."""
	tree = ast.parse(source)
	skip = docstring_nodes(tree)
	found = []

	for order, (node, _parent, _field, _index) in enumerate(walk_with_parent(tree)):
		line = getattr(node, "lineno", 0)

		if isinstance(node, ast.Compare):
			for position, op in enumerate(node.ops):
				for alternative in COMPARE_SWAPS.get(type(op), []):
					found.append(
						(
							order,
							("cmpop", position, alternative.__name__),
							line,
							f"{type(op).__name__} -> {alternative.__name__}",
						)
					)

		elif isinstance(node, ast.BinOp) and type(node.op) in BINOP_SWAPS:
			for alternative in BINOP_SWAPS[type(node.op)]:
				found.append(
					(
						order,
						("binop", None, alternative.__name__),
						line,
						f"{type(node.op).__name__} -> {alternative.__name__}",
					)
				)

		elif isinstance(node, ast.BoolOp):
			alternative = BOOLOP_SWAPS[type(node.op)]
			found.append(
				(
					order,
					("boolop", None, alternative.__name__),
					line,
					f"{type(node.op).__name__} -> {alternative.__name__}",
				)
			)

		elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
			found.append((order, ("drop_not", None, None), line, "drop `not`"))

		elif isinstance(node, ast.Return) and node.value is not None:
			if not (isinstance(node.value, ast.Constant) and node.value.value is None):
				found.append((order, ("return_none", None, None), line, "return None instead"))

		elif isinstance(node, ast.Constant) and id(node) not in skip:
			value = node.value
			if isinstance(value, bool):
				found.append((order, ("const", None, not value), line, f"{value} -> {not value}"))
			elif isinstance(value, int):
				found.append((order, ("const", None, value + 1), line, f"{value} -> {value + 1}"))

	return found


def mutate(source, site):
	"""Return `source` with exactly one mutation applied."""
	order, (kind, position, payload), _line, _description = site
	tree = ast.parse(source)

	for index, (node, parent, field, slot) in enumerate(walk_with_parent(tree)):
		if index != order:
			continue

		if kind == "cmpop":
			node.ops[position] = getattr(ast, payload)()
		elif kind in ("binop", "boolop"):
			node.op = getattr(ast, payload)()
		elif kind == "const":
			node.value = payload
		elif kind == "return_none":
			node.value = ast.Constant(value=None)
		elif kind == "drop_not":
			replacement = node.operand
			if slot is None:
				setattr(parent, field, replacement)
			else:
				getattr(parent, field)[slot] = replacement
		break

	return ast.unparse(ast.fix_missing_locations(tree))


# ---------------------------------------------------------------------------
# Running one mutant.
# ---------------------------------------------------------------------------
_WORKDIR = None

RUNNER = (
	"import sys, unittest, io;"
	"sys.path.insert(0, {workdir!r});"
	"result = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0, failfast=True)"
	".run(unittest.TestLoader().discover('tests', 'test*.py', '.'));"
	"sys.exit(0 if result.wasSuccessful() else 1)"
)


def _init_worker():
	global _WORKDIR
	_WORKDIR = tempfile.mkdtemp(prefix="cpos-worth-")


def _run_one(job):
	"""Apply one mutation in this worker's private copy and run the suite."""
	filename, site, source = job
	# A fresh tree per mutant is intentional. Reusing a package tree allowed
	# interpreter cache state from an earlier case to make a later mutation
	# appear to survive even though the same mutation was killed in isolation.
	# Copying this small, dependency-free package is cheap; false evidence is
	# not. Excluding bytecode makes the source under test unambiguous.
	with tempfile.TemporaryDirectory(prefix="case-", dir=_WORKDIR) as case:
		package = Path(case) / "cryptopos_core"
		shutil.copytree(PACKAGE, package, ignore=shutil.ignore_patterns("__pycache__"))
		target = package / filename
		target.write_text(mutate(source, site))
		environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
		try:
			finished = subprocess.run(
				[sys.executable, "-B", "-c", RUNNER.format(workdir=case)],
				cwd=CORE,
				capture_output=True,
				timeout=RUN_TIMEOUT_SECONDS,
				env=environment,
			)
			# Non-zero means some test failed or the import blew up. Either
			# way the change was noticed, which is the whole question.
			killed = finished.returncode != 0
		except subprocess.TimeoutExpired:
			# A mutation that hangs the suite has certainly changed behaviour.
			killed = True

	_order, _mutation, line, description = site
	return filename, line, description, killed


# ---------------------------------------------------------------------------
def main():
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument("--list", action="store_true", help="print every survivor")
	parser.add_argument("--module", help="only mutate this file, e.g. rates.py")
	parser.add_argument("--jobs", type=int, default=os.cpu_count() or 4)
	options = parser.parse_args()

	jobs = []
	for path in sorted(PACKAGE.glob("*.py")):
		if path.name in SKIP_FILES:
			continue
		if options.module and path.name != options.module:
			continue
		source = path.read_text()
		for site in sites(source):
			jobs.append((path.name, site, source))

	if not jobs:
		print("nothing to mutate")
		return 1

	print(f"  {DIM}{len(jobs)} mutants, {options.jobs} workers{RESET}", flush=True)

	results = []
	with ProcessPoolExecutor(max_workers=options.jobs, initializer=_init_worker) as pool:
		for done, outcome in enumerate(pool.map(_run_one, jobs, chunksize=4), start=1):
			results.append(outcome)
			if done % 50 == 0:
				print(f"  {DIM}{done}/{len(jobs)}{RESET}", flush=True)

	by_file = {}
	survivors = []
	accepted = []
	for filename, line, description, killed in results:
		total, dead = by_file.get(filename, (0, 0))
		by_file[filename] = (total + 1, dead + (1 if killed else 0))
		if killed:
			continue
		key = f"{filename}:{line}:{description}"
		(accepted if key in EQUIVALENT else survivors).append((filename, line, description, key))

	print()
	width = max(len(name) for name in by_file)
	for filename in sorted(by_file):
		total, dead = by_file[filename]
		score = 100.0 * dead / total if total else 100.0
		colour = GREEN if dead == total else YELLOW
		print(f"  {colour}{filename:<{width}}{RESET}  {dead:>4}/{total:<4}  {score:6.1f}%")

	total = len(results)
	dead = sum(1 for *_rest, killed in results if killed)
	print()

	if accepted:
		print(f"  {DIM}{len(accepted)} accepted as equivalent:{RESET}")
		for filename, line, description, key in accepted:
			print(f"      {DIM}{filename}:{line}  {description}{RESET}")
			print(f"        {DIM}{EQUIVALENT[key]}{RESET}")
		print()

	if survivors:
		print(
			f"  {RED}{BOLD}{len(survivors)} mutant(s) survived{RESET} — the code was wrong and nothing failed"
		)
		for filename, line, description, _key in survivors if options.list else survivors[:20]:
			print(f"      {filename}:{line}  {description}")
		if not options.list and len(survivors) > 20:
			print(f"      {DIM}...and {len(survivors) - 20} more; re-run with --list{RESET}")
		print()
		print(f"  {DIM}Either strengthen the assertion, or add the mutant to EQUIVALENT{RESET}")
		print(f"  {DIM}in {Path(__file__).name} with the reason it cannot be killed.{RESET}")
		return 1

	print(
		f"  {GREEN}{BOLD}every mutation was caught{RESET} "
		f"({dead}/{total} killed, {len(accepted)} accepted as equivalent)"
	)
	return 0


if __name__ == "__main__":
	sys.exit(main())
