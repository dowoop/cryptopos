"""Re-run the core suite whenever a source file changes.

Polling with stat() rather than inotify, for the same reason the package it
watches has no dependencies: `inotifywait` is not installed here, and asking
someone to apt-install a watcher before they can run tests is a worse trade
than one stat() per file every 400ms. The tree is a few dozen files.

    python tools/watch.py [paths...]
"""

import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORE = ROOT / "packages" / "cryptopos-core"
INTERVAL_SECONDS = 0.4

BOLD, DIM, GREEN, RED, RESET = "\033[1m", "\033[2m", "\033[32m", "\033[31m", "\033[0m"


def fingerprint(paths):
	"""Every watched file's mtime and size, as one comparable value."""
	stamps = []
	for path in paths:
		for file in sorted(path.rglob("*.py")):
			if "__pycache__" in file.parts:
				continue
			try:
				stat = file.stat()
			except FileNotFoundError:
				continue  # deleted between the glob and the stat
			stamps.append((str(file), stat.st_mtime_ns, stat.st_size))
	return tuple(stamps)


def run():
	started = time.monotonic()
	result = subprocess.run(
		[sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."],
		cwd=CORE,
		capture_output=True,
		text=True,
	)
	elapsed = time.monotonic() - started
	# unittest writes its summary to stderr.
	last = (result.stderr.strip().splitlines() or ["no output"])[-1]
	colour = GREEN if result.returncode == 0 else RED
	clock = datetime.now().strftime("%H:%M:%S")
	print(f"{DIM}{clock}{RESET}  {colour}{BOLD}{last}{RESET}  {DIM}{elapsed:.2f}s{RESET}", flush=True)
	if result.returncode != 0:
		print(result.stderr.rstrip(), flush=True)


def main():
	paths = [Path(arg).resolve() for arg in sys.argv[1:]] or [CORE / "src", CORE / "tests"]
	shown = ", ".join(str(p.relative_to(ROOT)) for p in paths)
	print(f"{BOLD}watching{RESET} {shown}  {DIM}ctrl-c to stop{RESET}", flush=True)

	seen = None
	try:
		while True:
			current = fingerprint(paths)
			if current != seen:
				seen = current
				run()
			time.sleep(INTERVAL_SECONDS)
	except KeyboardInterrupt:
		print("\nstopped")


if __name__ == "__main__":
	main()
