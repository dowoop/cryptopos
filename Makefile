# Development entry points for cryptopos.
#
# The package half (packages/cryptopos-core) is the part that can be developed
# on this machine alone: pure standard library, no framework, no database, no
# container. Everything below serves that loop.
#
# The Frappe app half (cryptopos/) cannot run here -- it needs a bench, a
# MariaDB and a Redis, which is what the Docker stack is for. `make` will not
# pretend otherwise; see DEVELOPMENT.md for that boundary.

CORE     := packages/cryptopos-core
VENV     := .venv
BUILDENVS := .buildenvs
PY       := $(CURDIR)/$(VENV)/bin/python
RUFF_VER := 0.16.3
RUFF     := uvx ruff@$(RUFF_VER)

# The versions the package promises to support. requires-python says >=3.9, so
# 3.9 is not a nice-to-have here -- it is the claim being tested.
MATRIX   := 3.9 3.11 3.13 3.14

.DEFAULT_GOAL := help
.PHONY: fit lockcheck help dev test terminal prove worth watch lint fmt matrix wheel check build dist-verify clean docker-check

help: ## Show this help
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk -F':.*?## ' '{printf "  \033[1m%-14s\033[0m %s\n", $$1, $$2}'
	@echo
	@echo "  Fast loop:  make test        Full gate:  make check"

dev: $(VENV)/.stamp ## Create .venv and install the core editable, with ruff

$(VENV)/.stamp: $(CORE)/pyproject.toml
	@# --allow-existing: the stamp goes stale every time pyproject.toml is
	@# touched, and without this the reinstall aborts on the venv that is
	@# already there -- taking `make test` down with it.
	uv venv --python 3.14 --allow-existing $(VENV)
	uv pip install --python $(PY) --quiet --editable $(CORE)
	uv pip install --python $(PY) --quiet ruff==$(RUFF_VER)
	@touch $@
	@echo "ready: $(PY)"

test: dev ## Run the core suite (fast loop)
	cd $(CORE) && $(PY) -m unittest discover -s tests -t . -q

terminal: ## Run the terminal suites (render + buttons), no browser needed
	@# Two files and not one: the first proves what each state LOOKS like,
	@# the second proves that clicking it DOES anything. A page whose handlers
	@# were never attached renders identically to one that works, so the
	@# second suite is the only thing standing between a green run and a
	@# terminal with every button disconnected.
	node tests/terminal_render_test.js
	node tests/terminal_button_test.js

prove: dev ## Fail if any function or control is never exercised
	@# Two halves of one claim. The Python gate reads line coverage from
	@# `trace`; the terminal gate reads its inventory out of the page source
	@# and diffs it against what the suites actually clicked. Neither list is
	@# maintained by hand, so neither can quietly go stale.
	@$(PY) tools/prove.py --list
	@node tools/prove_terminal.js

worth: dev ## Break the code on purpose; fail if the suites do not notice
	@# `prove` asks whether a line RAN. This asks whether the assertion around
	@# it was worth making, by rewriting one operator or constant at a time and
	@# checking that something fails. A survivor means the code was wrong and
	@# every test still passed.
	@#
	@# Survivors are triaged rather than banned: each tool carries an
	@# EQUIVALENT list of mutations that cannot change observable behaviour,
	@# with the reason. Anything not on that list fails.
	@$(PY) tools/worth.py
	@node tools/worth_terminal.js

watch: dev ## Re-run the core suite whenever a file changes
	@$(PY) tools/watch.py

lint: ## Lint everything ruff has an opinion about
	$(RUFF) check .

fmt: ## Sort imports and apply fixes, then format
	$(RUFF) check --fix .
	$(RUFF) format $(CORE)

matrix: ## Run the core suite on every supported Python, from source
	@# `uv python find --system` on purpose: without it uv hands back .venv
	@# whenever the requested version matches it, and the 3.14 row would
	@# quietly test the editable install instead of the source tree.
	@# Nothing is installed here -- a zero-dependency stdlib package needs no
	@# environment to run its own tests.
	@for v in $(MATRIX); do \
		printf '\033[1m python %-5s \033[0m ' $$v; \
		if ! exe=$$(uv python find --system $$v 2>/dev/null || uv python find $$v); then \
			echo "interpreter lookup failed"; exit 1; \
		fi; \
		if output=$$(cd $(CORE) && PYTHONPATH=src $$exe -m unittest discover -s tests -t . 2>&1); then \
			printf '%s\n' "$$output" | tail -1; \
		else \
			printf '%s\n' "$$output"; exit 1; \
		fi; \
	done

build: ## Build the wheel and sdist
	rm -rf $(CORE)/dist
	cd $(CORE) && uv build --quiet
	@ls -1 $(CORE)/dist

wheel: build ## Install the built wheel into a clean env per Python and test it
	@# An explicit throwaway venv per version rather than `uv run --with`,
	@# which reuses .venv when the version matches -- and testing a wheel
	@# against an editable install of the same source proves nothing.
	@# The suite runs with src/ NOT on the path, so it exercises the
	@# installed copy and the packaging assertions run rather than skip.
	@rm -rf $(BUILDENVS)
	@for v in $(MATRIX); do \
		printf '\033[1m python %-5s \033[0m ' $$v; \
		uv venv --quiet --python $$v $(BUILDENVS)/$$v >/dev/null 2>&1 || exit 1; \
		uv pip install --quiet --python $(BUILDENVS)/$$v/bin/python $(CORE)/dist/*.whl || exit 1; \
		if output=$$(cd $(CORE) && $(CURDIR)/$(BUILDENVS)/$$v/bin/python \
			-m unittest discover -s tests -t . 2>&1); then \
			printf '%s\n' "$$output" | tail -1; \
		else \
			printf '%s\n' "$$output"; exit 1; \
		fi; \
	done
	@rm -rf $(BUILDENVS)

dist-verify: build ## Print SHA256 of the built artifacts, for a release record
	@# A zero-dependency package's supply chain is its own bytes, so the
	@# bytes are what a release note should carry. Anyone can recompute these
	@# from the sdist and compare.
	@cd $(CORE)/dist && sha256sum *.whl *.tar.gz

check: lint matrix wheel prove terminal worth ## Everything CI would run
	@echo
	@echo "lint clean, suite green on $(MATRIX), wheel installs and passes,"
	@echo "every line executes, every control responds, and breaking any of"
	@echo "it on purpose is caught."

docker-check: ## Report whether the Frappe stack is reachable from this shell
	@docker ps >/dev/null 2>&1 \
		&& echo "docker: reachable -- the app half can be exercised" \
		|| { echo "docker: NOT reachable from this shell."; \
		     echo "  the package half needs nothing; the Frappe app half does."; \
		     echo "  fix: sudo usermod -aG docker $$USER   (then log out and back in)"; }

clean: ## Remove build output, caches and the dev venv
	rm -rf $(VENV) $(BUILDENVS) $(CORE)/dist
	find . -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
	find . -name .ruff_cache -type d -prune -exec rm -rf {} + 2>/dev/null || true

# The measurement behind D11, re-runnable. Not in `make prove`: it needs the
# network, and a public endpoint being slow is not a defect in this repository.
lockcheck: ## Measure a chain's settlement gate against the rate lock (D11, D15)
	python3 tools/lockcheck.py

# Is this deployment fit to be shown to strangers? Three findings, three gates.
#
# Deliberately NOT part of `make check`: two of these need a live site and the
# third needs the network, and neither is a property of this source tree. This
# answers a question about a DEPLOYMENT, which is why it is its own word.
#
# It runs what it can from here and names what it cannot, rather than skipping
# quietly -- a fitness check that passes because it did not look is the failure
# mode it exists to prevent.
fit: ## Can this deployment be shown to strangers? (D5, D11, D15)
	@echo "=== the settlement gate, against the rate lock (D11, D15) ==="
	@python3 tools/lockcheck.py || true
	@echo
	@echo "=== the two gates that need a live site ==="
	@echo "  These read the database, so they run against the bench, not here:"
	@echo
	@echo "    bench --site erp.localhost execute cryptopos.tools.rails_probe.run"
	@echo "        does any enabled rail receive at an address another shares? (D5)"
	@echo
	@echo "    bench --site erp.localhost execute cryptopos.tools.isolation_probe.run"
	@echo "        can one visitor read another's sale? (D11 finding 2)"
	@echo
	@echo "    bench --site erp.localhost execute cryptopos.tools.reorg_probe.run"
	@echo "        does every booked sale still have a live transaction? (D15)"
	@echo
	@echo "=== and the one that proves the whole path, not the parts (D19) ==="
	@echo "  Spends testnet money, so it lives beside the payer and refuses by default:"
	@echo
	@echo "    cd '../Point of Sale' && python3 prove_end_to_end.py --send"
	@echo "        charge -> pay -> settle -> book, asserted end to end"
	@echo
	@echo "  Both must refuse before this deployment is public. See GOAL.md."
