"""Fail if any line of `cryptopos_core` is never executed by its own suite.

    python tools/prove.py [--list]

The claim this defends is narrow and worth stating exactly, because coverage
tools are routinely read as claiming more than they do.

**What this proves:** every executable statement in the package runs at least
once during the suite. Nothing in the source is unreachable, unreferenced, or
kept alive only by being imported.

**What it does not prove:** that the assertions around those statements are
the right ones. A line can be executed by a test that asserts nothing about
it. Coverage is a floor, not a ceiling — it catches the failure mode where a
function is quietly dead, or where a refusal branch has never once been taken
and nobody noticed. Both of those were real here: the three rate-feed
adapters had never executed, and the shielded-Zcash refusal was reached by a
fixture that failed one check earlier, so the branch the test was named for
had never run while the test passed.

Implemented on `trace` from the standard library rather than `coverage`, for
the same reason the package it measures has no dependencies: a gate that has
to be pip-installed before it can run is a gate people stop running.

Three exclusions, all deliberate:

    qrcodegen.py     vendored unchanged from Project Nayuki. Its unused
                     branches are upstream's, and covering them would mean
                     writing tests for code this package must not edit.

    bare constants   docstrings and ``...`` protocol bodies have no runtime
                     effect and are not behavioral statements.

    `global` / `nonlocal`   declarations, not statements. They compile to no
                     bytecode, so no tracer can ever record them.

    no line-table entry     a multiline ``if (`` header can be an AST statement
                     while every bytecode instruction is attributed to its
                     first condition line. The AST inventory is intersected
                     with compiled line tables so the denominator contains
                     only locations the stdlib tracer can possibly report.
"""

import ast
import dis
import io
import os
import re
import sys
import threading
import trace
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORE = ROOT / "packages" / "cryptopos-core"
PACKAGE = CORE / "src" / "cryptopos_core"
REGISTER = ROOT / "PROOF.md"

VENDORED = {"qrcodegen.py"}

# `__init__.py` is re-exports; its symbols are the ones listed under their own
# modules, and rowing them twice in the register would be noise.
UNREGISTERED = VENDORED | {"__init__.py"}

BOLD, DIM, GREEN, RED, RESET = "\033[1m", "\033[2m", "\033[32m", "\033[31m", "\033[0m"


def executable_lines(tree, filename="<proof>"):
	"""Every line `trace` could report, given a module's AST.

	Bare constants and `global`/`nonlocal` declarations have no runtime effect.
	Multiline statement headers can also have no line-table entry because the
	interpreter attributes their bytecode to the first condition line instead.
	Intersecting the AST statements with every nested code object's line table
	keeps the denominator limited to lines the stdlib tracer can actually see.
	A `def` or `class` line still executes at import, when the object is bound.
	"""
	lines = set()
	for node in ast.walk(tree):
		if not isinstance(node, ast.stmt):
			continue
		if isinstance(node, (ast.Global, ast.Nonlocal)):
			continue
		if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
			continue
		lines.add(node.lineno)
	root_code = compile(tree, filename, "exec")
	code_lines = set()
	pending = [root_code]
	while pending:
		code = pending.pop()
		code_lines.update(lineno for _offset, lineno in dis.findlinestarts(code))
		pending.extend(constant for constant in code.co_consts if isinstance(constant, type(code)))
	return lines & code_lines


def symbols_of(tree, prefix=""):
	"""Every function, method and class a module defines, qualified."""
	found = []
	for child in tree.body:
		if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
			found.append(prefix + child.name)
		elif isinstance(child, ast.ClassDef):
			found.append(prefix + child.name)
			found.extend(symbols_of(child, prefix + child.name + "."))
	return found


def check_register():
	"""Every symbol must be NOTED as well as executed.

	Coverage says a function ran. It cannot say what the function is for, and
	a package whose reason for existing lives only in the head of whoever
	wrote it is one refactor away from losing it. So PROOF.md carries a row
	per symbol, and this fails when one is added without one.

	Matching is on the bare name inside a code span, so `OotleReader.promise`
	in the register satisfies `promise`. Deliberately lenient: the gate is
	there to catch a symbol nobody wrote anything about, not to police how
	the row is phrased.
	"""
	if not REGISTER.exists():
		return [f"{REGISTER.name} is missing"]

	# Fenced blocks first: ``` pairs with ``` and would otherwise swallow a
	# whole section into one "code span", desynchronising every single-tick
	# pair after it. Single spans are then read line by line, so an unclosed
	# backtick costs one line rather than the rest of the file.
	text = re.sub(r"```.*?```", " ", REGISTER.read_text(), flags=re.S)

	noted = set()
	for span in re.findall(r"`([^`\n]+)`", text):
		for part in span.split("."):
			noted.add(part)
			noted.add(part.strip("()*_ "))

	missing = []
	for path in sorted(PACKAGE.glob("*.py")):
		if path.name in UNREGISTERED:
			continue
		for symbol in symbols_of(ast.parse(path.read_text())):
			if symbol.split(".")[-1] not in noted:
				missing.append(f"{path.name}::{symbol}")
	return missing


def run_suite():
	"""Run the core suite in-process, returning (tests, failures, errors)."""
	suite = unittest.TestLoader().discover(start_dir="tests", top_level_dir=".")
	result = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(suite)
	return result.testsRun, len(result.failures) + len(result.errors)


def measure():
	"""Run the suite under the tracer. Returns (hits, tests, failed)."""
	os.chdir(CORE)
	sys.path.insert(0, str(CORE / "src"))
	sys.path.insert(0, str(CORE))

	outcome = {}

	def record():
		outcome["tests"], outcome["failed"] = run_suite()

	# Do not use `ignoredirs` here. `trace` caches that decision by MODULE
	# BASENAME, not by path: after ignoring a stdlib module named `errors`, it
	# also ignored this package's `errors.py` and reported the whole file as
	# uncovered. We filter the recorded filenames to PACKAGE below anyway, so
	# counting stdlib execution costs a little memory and keeps the evidence
	# correct.
	tracer = trace.Trace(count=1, trace=0)
	# `trace.Trace.runfunc` installs its hook only on the calling thread.
	# Feed requests deliberately run in workers, and excluding those workers
	# would make production code look dead even while the tests execute it.
	threading.settrace(tracer.globaltrace)
	try:
		tracer.runfunc(record)
	finally:
		threading.settrace(None)

	hits = {}
	for (filename, lineno), _count in tracer.results().counts.items():
		hits.setdefault(os.path.abspath(filename), set()).add(lineno)
	return hits, outcome.get("tests", 0), outcome.get("failed", 0)


def main():
	listing = "--list" in sys.argv
	hits, tests, failed = measure()

	if failed:
		print(f"{RED}{BOLD}the suite is not green -- fix that before reading coverage{RESET}")
		return 1

	total_missing = 0
	total_lines = 0
	report = []

	for path in sorted(PACKAGE.glob("*.py")):
		if path.name in VENDORED:
			continue
		source = path.read_text()
		wanted = executable_lines(ast.parse(source), str(path))
		covered = wanted & hits.get(str(path), set())
		missing = sorted(wanted - covered)
		total_lines += len(wanted)
		total_missing += len(missing)
		report.append((path, len(covered), len(wanted), missing, source.splitlines()))

	width = max(len(path.name) for path, *_ in report)
	for path, covered, wanted, missing, lines in report:
		colour = GREEN if not missing else RED
		percent = 100.0 * covered / wanted if wanted else 100.0
		print(f"  {colour}{path.name:<{width}}{RESET}  {covered:>4}/{wanted:<4}  {percent:6.1f}%")
		if missing and listing:
			for lineno in missing:
				print(f"      {DIM}{lineno:>4}{RESET}  {lines[lineno - 1].strip()[:96]}")

	print()
	percent = 100.0 * (total_lines - total_missing) / total_lines if total_lines else 100.0
	if total_missing:
		print(
			f"  {RED}{BOLD}{total_missing} line(s) of cryptopos_core never execute{RESET} "
			f"({percent:.1f}% of {total_lines}, {tests} tests)"
		)
		if not listing:
			print(f"  {DIM}re-run with --list to see them{RESET}")
		return 1

	unregistered = check_register()
	if unregistered:
		print(f"  {RED}{BOLD}{len(unregistered)} symbol(s) have no row in {REGISTER.name}{RESET}")
		for symbol in unregistered:
			print(f"      {DIM}unnoted{RESET}  {symbol}")
		print(f"  {DIM}every function is executed; these are not explained anywhere{RESET}")
		return 1

	print(f"  {GREEN}{BOLD}every line of cryptopos_core executes{RESET} ({total_lines} lines, {tests} tests)")
	print(f"  {GREEN}{BOLD}every symbol has a row in {REGISTER.name}{RESET}")
	return 0


if __name__ == "__main__":
	sys.exit(main())
