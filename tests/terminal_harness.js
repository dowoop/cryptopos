/* The desk, stubbed far enough that the terminal can actually be USED.
 *
 * The first version of this stub answered every `find()` with a chainable
 * no-op. That is enough to read the HTML a render produced, and it silently
 * made `wire()` — every click handler on the page — unreachable: handlers
 * were registered against an object that discarded them, so a suite could go
 * green with every button on the terminal disconnected.
 *
 * So this file carries a small real DOM instead. It is not a browser: there
 * is no layout, no CSS, no focus and no bubbling. What it does have is the
 * three things a button needs to be provable —
 *
 *     elements    parsed out of the HTML a render actually produced, so a
 *                 control that stops being rendered stops being findable
 *     handlers    registered per element by the page's own `wire()`, and
 *                 re-registered on every render, so a handler lost to a
 *                 re-render is a failing test rather than a dead button
 *     dispatch    `click()` and `change()` call what `wire()` attached,
 *                 with the `currentTarget` the handler reads
 *
 * `frappe.call` is a routed fake rather than a fixed answer, because half of
 * what a button does is decided by what the server said back — and the
 * failure paths (a refused charge, an unreachable poll) are exactly the ones
 * nobody reproduces by hand.
 */

const fs = require("fs");
const path = require("path");
const vm = require("vm");

// ---------------------------------------------------------------------------
// A very small HTML reader.
//
// Attributes and tag names only. That is all any selector in the page uses,
// and a real parser would be a dependency this repo does not take.
// ---------------------------------------------------------------------------
const TAG = /<([a-zA-Z][\w-]*)((?:\s+[-\w:]+(?:="[^"]*")?)*)\s*\/?>/g;
const ATTR = /([-\w:]+)(?:="([^"]*)")?/g;

function parseElements(html) {
	const elements = [];
	TAG.lastIndex = 0;
	let match;
	while ((match = TAG.exec(html))) {
		const [whole, tag, rawAttrs] = match;
		const attrs = {};
		ATTR.lastIndex = 0;
		let attr;
		while ((attr = ATTR.exec(rawAttrs || ""))) {
			attrs[attr[1]] = attr[2] === undefined ? "" : attr[2];
		}
		const openEnds = match.index + whole.length;
		const closeAt = html.indexOf(`</${tag}`, openEnds);
		elements.push({
			__isElement: true,
			tag,
			attrs,
			text: closeAt === -1 ? "" : html.slice(openEnds, closeAt),
			// Live properties a handler reads off `currentTarget`.
			value: attrs.value,
			checked: "checked" in attrs,
			disabled: "disabled" in attrs,
			handlers: {},
		});
	}
	// A <select> reports the value of its selected <option>.
	for (let i = 0; i < elements.length; i++) {
		if (elements[i].tag !== "select") continue;
		for (let j = i + 1; j < elements.length && elements[j].tag === "option"; j++) {
			if ("selected" in elements[j].attrs) elements[i].value = elements[j].attrs.value;
		}
	}
	return elements;
}

function matches(element, selector) {
	const attribute = selector.match(/^\[([-\w]+)(?:="([^"]*)")?\]$/);
	if (attribute) {
		return attribute[2] === undefined
			? attribute[1] in element.attrs
			: element.attrs[attribute[1]] === attribute[2];
	}
	if (selector.startsWith(".")) {
		return (element.attrs.class || "").split(/\s+/).includes(selector.slice(1));
	}
	return element.tag === selector;
}

function fire(element, event, extra = {}) {
	const identity =
		element.attrs["data-act"] !== undefined
			? `[data-act="${element.attrs["data-act"]}"]`
			: element.attrs["data-key"] !== undefined
			? `[data-key="${element.attrs["data-key"]}"]`
			: "." + (element.attrs.class || element.tag).split(/\s+/)[0];
	record("control", `${identity} ${event}`);
	const handlers = element.handlers[event] || [];
	const shaped = Object.assign({ currentTarget: element, target: element }, extra);
	handlers.forEach((handler) => handler(shaped));
	return handlers.length;
}


// ---------------------------------------------------------------------------
// Coverage instrumentation.
//
// Off unless CPOS_COVERAGE names a file. `tools/prove_terminal.js` sets it,
// runs both suites as subprocesses, and reads back what was reached — which
// turns "every function and every button is exercised" into something a gate
// checks rather than something a reader counts by hand.
// ---------------------------------------------------------------------------
const COVERAGE_FILE = process.env.CPOS_COVERAGE || "";
const REACHED = new Set();

function record(kind, name) {
	if (COVERAGE_FILE) REACHED.add(`${kind}\t${name}`);
}

if (COVERAGE_FILE) {
	process.on("exit", () => {
		if (!REACHED.size) return;
		fs.appendFileSync(COVERAGE_FILE, [...REACHED].join("\n") + "\n");
	});
}

/** Wrap every method on the page class so calling one is observable. */
function instrument(Terminal, frappe) {
	const proto = Terminal.prototype;
	for (const name of Object.getOwnPropertyNames(proto)) {
		if (name === "constructor") continue;
		const descriptor = Object.getOwnPropertyDescriptor(proto, name);
		if (!descriptor || typeof descriptor.value !== "function") continue;
		const original = descriptor.value;
		proto[name] = function (...args) {
			record("method", name);
			return original.apply(this, args);
		};
	}

	// The desk's real entry point, which lives on frappe.pages rather than on
	// the class. A suite that only ever constructs the class directly leaves
	// the one function the framework actually calls unexecuted.
	const page = frappe.pages["terminal"];
	if (page && typeof page.on_page_load === "function") {
		const original = page.on_page_load;
		page.on_page_load = function (...args) {
			record("method", "on_page_load");
			return original.apply(this, args);
		};
	}

	return new Proxy(Terminal, {
		construct(target, args) {
			record("method", "constructor");
			return new target(...args);
		},
	});
}

// ---------------------------------------------------------------------------
// jQuery, to the exact extent the page uses it.
// ---------------------------------------------------------------------------
function makeJQuery() {
	const stateFor = (node) => {
		if (!node.__state) node.__state = { html: "", elements: [], handlers: {} };
		return node.__state;
	};

	const nothing = {
		html: () => nothing,
		find: () => nothing,
		on: () => nothing,
		is: () => false,
		data: () => undefined,
	};

	function collection(elements) {
		return {
			length: elements.length,
			on(event, handler) {
				const name = event.split(".")[0];
				elements.forEach((element) => {
					(element.handlers[name] = element.handlers[name] || []).push(handler);
				});
				return this;
			},
			get(index) {
				return elements[index];
			},
		};
	}

	function nodeWrapper(node) {
		const state = stateFor(node);
		return {
			html(value) {
				if (value === undefined) return state.html;
				// A render replaces the tree, so every handler registered
				// against the old one goes with it. That is what a browser
				// does, and it is why `wire()` has to run again.
				state.html = value;
				state.elements = parseElements(value);
				return this;
			},
			find(selector) {
				return collection(state.elements.filter((element) => matches(element, selector)));
			},
			on(event, handler) {
				const name = event.split(".")[0];
				(state.handlers[name] = state.handlers[name] || []).push(handler);
				return this;
			},
			// The page asks this of its own body to decide whether a keypress
			// is meant for it. A detached terminal must not eat the keyboard.
			is: (selector) => selector === ":visible",
		};
	}

	function elementWrapper(element) {
		return {
			data: (name) => element.attrs["data-" + name],
			// `$(event.target).is("input, textarea, select")` — the guard that
			// keeps the keypad from hijacking typing in a form field.
			is: (selector) =>
				selector
					.split(",")
					.map((part) => part.trim())
					.includes(element.tag),
			on: () => elementWrapper(element),
			find: () => nothing,
			html: () => "",
		};
	}

	const $ = (node) => {
		if (node === undefined || node === null || typeof node === "string") return nothing;
		if (node.__isElement) return elementWrapper(node);
		return nodeWrapper(node);
	};
	$.fn = {};
	$.stateFor = stateFor;
	return $;
}

// ---------------------------------------------------------------------------
// The server half of every button.
// ---------------------------------------------------------------------------
function makeServer() {
	return {
		routes: {},
		calls: [],
		answer(method, responder) {
			this.routes[method] = responder;
			return this;
		},
		callsTo(method) {
			return this.calls.filter((call) => call.method === method);
		},
		call({ method, args }) {
			this.calls.push({ method, args });
			const route = this.routes[method];
			if (route === undefined) {
				return Promise.reject(new Error(`no route stubbed for ${method}`));
			}
			const answer = typeof route === "function" ? route(args) : route;
			if (answer instanceof Error) return Promise.reject(answer);
			return Promise.resolve({ message: answer });
		},
	};
}

/** A rejection shaped the way Frappe rejects, so `reason_from` is exercised. */
function serverRefusal(message) {
	const error = new Error("Internal Server Error");
	error._server_messages = JSON.stringify([JSON.stringify({ message })]);
	return error;
}

// ---------------------------------------------------------------------------
// Timers, so the auto-poll interval can be proved without waiting for it.
// ---------------------------------------------------------------------------
function makeTimers() {
	return {
		next: 1,
		active: new Map(),
		cleared: [],
		set(fn, ms) {
			const id = this.next++;
			this.active.set(id, { fn, ms });
			return id;
		},
		clear(id) {
			this.cleared.push(id);
			this.active.delete(id);
		},
		/** Run every live interval once, as the clock would. */
		tick() {
			[...this.active.values()].forEach((timer) => timer.fn());
		},
	};
}

// ---------------------------------------------------------------------------
// Load the page into a sandbox.
// ---------------------------------------------------------------------------
function load() {
	// CPOS_PAGE lets `tools/worth_terminal.js` point the suites at a mutated
	// copy of the page. Unset -- which is every normal run -- it reads the
	// real one, so nothing about the default path depends on the mutation
	// tool existing.
	const page = process.env.CPOS_PAGE
		? path.resolve(process.env.CPOS_PAGE)
		: path.join(__dirname, "..", "cryptopos", "cryptopos", "page", "terminal", "terminal.js");
	const source = fs.readFileSync(page, "utf8");

	const $ = makeJQuery();
	const server = makeServer();
	const timers = makeTimers();
	const styles = [];

	const frappe = {
		// The desk pre-creates frappe.pages[name] before loading the script,
		// so the stub does the same lazily.
		pages: new Proxy(
			{},
			{
				get(target, key) {
					if (!(key in target)) target[key] = {};
					return target[key];
				},
			}
		),
		ui: { make_app_page: ({ parent }) => ({ body: parent }) },
		utils: {
			escape_html: (s) =>
				String(s == null ? "" : s).replace(
					/[&<>"']/g,
					(c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]
				),
		},
		call: (options) => server.call(options),
	};

	const documentStub = {
		__isDocument: true,
		getElementById: () => null,
		createElement: () => ({ style: {}, setAttribute() {} }),
		head: { appendChild: (node) => styles.push(node) },
	};

	const sandbox = {
		$,
		frappe,
		document: documentStub,
		__: (s) => s,
		setInterval: (fn, ms) => timers.set(fn, ms),
		clearInterval: (id) => timers.clear(id),
		console,
		BigInt,
		Promise,
	};
	sandbox.window = sandbox;
	sandbox.globalThis = sandbox;

	// The file does not export; expose the class by appending an assignment.
	vm.runInNewContext(source + "\n;globalThis.__Terminal = CryptoPosTerminal;", sandbox);

	const Terminal = COVERAGE_FILE ? instrument(sandbox.__Terminal, frappe) : sandbox.__Terminal;

	return { Terminal, sandbox, server, timers, $, styles, documentStub };
}

// ---------------------------------------------------------------------------
// Driving the surface: what a cashier's hands do.
// ---------------------------------------------------------------------------
function elementsOf(terminal) {
	return terminal.page.body.__state ? terminal.page.body.__state.elements : [];
}

function findAll(terminal, selector) {
	return elementsOf(terminal).filter((element) => matches(element, selector));
}

function find(terminal, selector) {
	return findAll(terminal, selector)[0] || null;
}

function exists(terminal, selector) {
	return find(terminal, selector) !== null;
}

/** Click a rendered control. Throws if it is not on screen — not being there is a result. */
function click(terminal, selector) {
	const element = find(terminal, selector);
	if (!element) throw new Error(`nothing matching ${selector} is on screen to click`);
	fire(element, "click");
	return element;
}

/** Tick or untick a checkbox and fire the change its handler listens for. */
function setChecked(terminal, selector, checked) {
	const element = find(terminal, selector);
	if (!element) throw new Error(`nothing matching ${selector} is on screen`);
	element.checked = checked;
	fire(element, "change");
	return element;
}

/** Choose an option in a <select> and fire the change. */
function choose(terminal, selector, value) {
	const element = find(terminal, selector);
	if (!element) throw new Error(`nothing matching ${selector} is on screen`);
	element.value = value;
	fire(element, "change");
	return element;
}

/** Press a key, as the real keyboard would, at the document. */
function press(harness, key, target) {
	const state = harness.$.stateFor(harness.documentStub);
	const handlers = state.handlers.keydown || [];
	record("key", key);
	let prevented = false;
	const event = {
		key,
		target,
		preventDefault: () => {
			prevented = true;
		},
	};
	handlers.forEach((handler) => handler(event));
	return prevented;
}

/** Let every pending `frappe.call` settle and its `.then` run. */
function settle() {
	return new Promise((resolve) => setImmediate(resolve));
}

// ---------------------------------------------------------------------------
// Reporting.
// ---------------------------------------------------------------------------
class Reporter {
	constructor(title) {
		this.title = title;
		this.passed = [];
		this.failed = [];
	}

	check(rule, condition, detail = "") {
		(condition ? this.passed : this.failed).push(rule + (detail ? ` -- ${detail}` : ""));
	}

	report() {
		console.log("");
		this.passed.forEach((line) => console.log(`  PASS  ${line}`));
		this.failed.forEach((line) => console.log(`  FAIL  ${line}`));
		console.log("");
		console.log(`  ${this.title}: ${this.passed.length} passed, ${this.failed.length} failed`);
		process.exit(this.failed.length ? 1 : 0);
	}
}

// ---------------------------------------------------------------------------
// Fixtures, shared so the two suites cannot drift apart.
//
// `sale()` is the shape `cryptopos.api.charge` and `cryptopos.api.status`
// return. It is defined once because a field added on the server and added to
// only one of these files is exactly the drift a shared fixture prevents.
// ---------------------------------------------------------------------------
const RAILS = [
	{
		name: "btc",
		label: "Bitcoin / BTC",
		asset: "BTC",
		maturity: "partial",
		maturity_note: "real testnet4 reads against a shared configured address",
		gate_text: "confs >= 3 (mainnet; testnet settles at 1)",
	},
	{
		name: "eth",
		label: "Ethereum / ETH",
		asset: "ETH",
		maturity: "works",
		maturity_note: "",
		gate_text: "confs >= 12",
	},
];

function sale(overrides = {}) {
	return Object.assign(
		{
			name: "CPS-2026-00001",
			state: "awaiting",
			state_word: "AWAITING",
			end_kind: "",
			review_reason: "",
			mode: "testnet",
			provenance: "REAL",
			uri: "bitcoin:tb1qexample?amount=0.0004",
			qr_modules: { size: 3, quiet: 4, rows: ["101", "010", "101"] },
			usd_cents: 4200,
			invoiced_native: "39685",
			credited_native: "0",
			sighted_native: "0",
			unit_name: "satoshi",
			gate_text: "confs >= 3 (mainnet; testnet settles at 1)",
			binding: "shared",
			identity_source: "config",
			identity_address: "tb1qexample",
			rate_microcents: 6400000000,
			rate_source: "coinbase+kraken",
			rate_at: "2026-08-15 20:00:00",
			rate_lock_end: "2026-08-15 20:15:00",
			tx_id: "",
			settled_at: "",
			invoice_id: "INV-20260815-0001",
			invoice_ref: "K7M2-P9QX-4TWN",
			sales_invoice: null,
			bookable: false,
			not_bookable_because: "not settled (state is awaiting)",
			events: [],
		},
		overrides
	);
}

module.exports = {
	load,
	Reporter,
	parseElements,
	matches,
	fire,
	find,
	findAll,
	exists,
	click,
	setChecked,
	choose,
	press,
	settle,
	serverRefusal,
	elementsOf,
	RAILS,
	sale,
};
