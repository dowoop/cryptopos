/* Render checks for the terminal page, run without a browser.
 *
 *   node tests/terminal_render_test.js
 *
 * The page is 600-odd lines of string building, and the parts most likely to
 * be wrong are the ones nobody looks at until they matter: the QR path, and
 * the copy for the three endings that are not "settled". Those endings are
 * rare at the counter by construction, which is exactly why they need a
 * check that does not depend on someone reproducing them by hand.
 *
 * This stubs enough of jQuery and frappe to instantiate the class and read
 * the HTML it produces. It asserts on rendered output, not on internals.
 */

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const PASS = [];
const FAIL = [];

function check(rule, condition, detail = "") {
	(condition ? PASS : FAIL).push(rule + (detail ? ` -- ${detail}` : ""));
}

// ---------------------------------------------------------------------------
// Just enough of the desk to instantiate the page.
// ---------------------------------------------------------------------------
function makeStubs() {
	let captured = "";

	const chainable = {
		html(v) {
			if (v !== undefined) captured = v;
			return v === undefined ? captured : chainable;
		},
		find() {
			return chainable;
		},
		on() {
			return chainable;
		},
		is() {
			return true;
		},
	};

	const $ = () => chainable;
	$.fn = {};

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
					(c) =>
						({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]
				),
		},
		call: () => Promise.resolve({ message: [] }),
	};

	const document = {
		getElementById: () => null,
		createElement: () => ({ style: {}, setAttribute() {} }),
		head: { appendChild() {} },
	};

	return { $, frappe, document, captured: () => captured };
}

// ---------------------------------------------------------------------------
// Load the page module into a sandbox.
// ---------------------------------------------------------------------------
const source = fs.readFileSync(
	path.join(__dirname, "..", "cryptopos", "cryptopos", "page", "terminal", "terminal.js"),
	"utf8"
);

const stubs = makeStubs();
const sandbox = {
	$: stubs.$,
	frappe: stubs.frappe,
	document: stubs.document,
	__: (s) => s,
	setInterval: () => 1,
	clearInterval: () => {},
	console,
	BigInt,
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;

// Expose the class by appending an assignment; the file itself does not export.
vm.runInNewContext(source + "\n;globalThis.__Terminal = CryptoPosTerminal;", sandbox);
const Terminal = sandbox.__Terminal;
check("the page module loads and defines the terminal class", typeof Terminal === "function");

// ---------------------------------------------------------------------------
// Fixtures
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
];

function terminal() {
	const t = new Terminal({ body: {} });
	t.rails = RAILS;
	t.rail = "btc";
	return t;
}

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

// ---------------------------------------------------------------------------
// 1. The keypad is the home.
// ---------------------------------------------------------------------------
{
	const t = terminal();
	const html = t.keypad_html();
	check("keypad renders all ten digits", [..."0123456789"].every((d) => html.includes(`data-key="${d}"`)));
	check("keypad renders a decimal key", html.includes('data-key="."'));
	check("keypad renders a backspace key", html.includes('data-key="back"'));
	check("charge is disabled with no amount keyed", html.includes("disabled"));
	check(
		"the settle gate ships on the surface that offers the charge",
		html.includes("confs &gt;= 3") || html.includes("confs >= 3")
	);
	check(
		"a rail that is not 'works' says so before the customer is waiting",
		html.includes("partial") && html.includes("shared configured address")
	);

	t.key("4");
	t.key("2");
	check("keying digits builds the amount", t.amount === "42", t.amount);
	check("the amount converts to cents", t.cents() === 4200, String(t.cents()));
	t.key(".");
	t.key("5");
	t.key("0");
	check("decimal entry works", t.cents() === 4250, String(t.cents()));
	t.key("9");
	check("a third decimal place is refused", t.cents() === 4250, t.amount);
	t.key("back");
	check("backspace removes a digit", t.amount === "42.5", t.amount);
}

// ---------------------------------------------------------------------------
// 2. The awaiting screen, which the customer sees.
// ---------------------------------------------------------------------------
{
	const t = terminal();
	t.sale = sale();
	const html = t.awaiting_html();
	check("awaiting renders an svg", html.includes("<svg"));
	check("the QR draws one rect per dark module", (html.match(/M\d+,\d+h1v1h-1z/g) || []).length === 5);
	check("the QR includes the quiet zone in its viewBox", html.includes('viewBox="0 0 11 11"'));
	check("the native amount is shown", html.includes("39,685"));
	check("the unit is named", html.includes("satoshi"));
	check("the fiat amount is shown", html.includes("$42.00"));
	check("the gate is shown beside the promise", html.includes("confs &gt;= 3"));
	check("the rate source is attributed", html.includes("coinbase+kraken"));
	check("a poll control exists", html.includes('data-act="poll"'));
	check("auto-poll is offered", html.includes('data-act="autopoll"'));
	check("testnet is flagged on the customer-facing screen", html.includes("testnet"));
	check("a shared address is disclosed", html.includes("shared address"));
}

// ---------------------------------------------------------------------------
// 3. Four endings, and each says only what it can stand behind.
// ---------------------------------------------------------------------------
{
	const t = terminal();

	t.sale = sale({ state: "confirmed", end_kind: "clean", credited_native: "39685", sales_invoice: "ACC-SINV-0001", bookable: true });
	let html = t.done_html();
	check("a clean settle says SETTLED", html.includes("SETTLED"));
	check("a settled sale links its invoice", html.includes("ACC-SINV-0001"));

	t.sale = sale({ state: "confirmed", end_kind: "over", credited_native: "1000000", sales_invoice: "ACC-SINV-0002", bookable: true });
	html = t.done_html();
	check("an overpayment is named as one", html.includes("More arrived than was invoiced"));
	check("an overpayment shows both figures", html.includes("1,000,000") && html.includes("39,685"));

	t.sale = sale({ state: "expired", end_kind: "clean" });
	html = t.done_html();
	check("an expiry with nothing seen says EXPIRED", html.includes("EXPIRED"));
	check("an expired sale reports not booked", html.includes("Not booked"));

	t.sale = sale({
		state: "needs_review",
		end_kind: "unidentified",
		sighted_native: "50000",
		review_reason: "50000 satoshi arrived and could not be tied to this sale.",
	});
	html = t.done_html();
	check("sighted-but-unbindable parks as NEEDS REVIEW", html.includes("NEEDS REVIEW"));
	check("the parked reason is shown in full", html.includes("could not be tied to this sale"));
	check(
		"sighted money is shown and marked unbooked",
		html.includes("50,000") && html.includes("not booked")
	);

	t.sale = sale({
		state: "needs_review",
		end_kind: "unverified",
		review_reason: "The rate lock ran out and the last look never reached the chain.",
	});
	html = t.done_html();
	check(
		"an unreachable final look does not claim the sale was unpaid",
		html.includes("cannot be called either way") && !html.includes("Nothing arrived")
	);
}

// ---------------------------------------------------------------------------
// 4. Hiding the log may hide an explanation, never a refusal.
// ---------------------------------------------------------------------------
{
	const t = terminal();
	t.show_log = false;
	t.note_error("endpoint refused the request");
	check("an error with the log closed is held for the terminal", t.unseen_error !== null);
	check(
		"the held error is rendered on the terminal card",
		t.keypad_html().includes("endpoint refused the request")
	);

	const t2 = terminal();
	t2.show_log = true;
	t2.note_error("endpoint refused the request");
	check("with the log open the notice is not duplicated", t2.unseen_error === null);
}

// ---------------------------------------------------------------------------
// 5. Developer surfaces are opt-in and off by default.
// ---------------------------------------------------------------------------
{
	const t = terminal();
	check("the dev bench is off on first open", t.show_bench === false);
	check("the activity log is off on first open", t.show_log === false);

	const closed = t.panels_html();
	check("with both closed no props table is rendered", !closed.includes("cpos-props"));
	check("with both closed no log table is rendered", !closed.includes("cpos-log"));
	check("both toggles are offered", closed.includes('data-act="bench"') && closed.includes('data-act="log"'));

	t.sale = sale({ tx_id: "abc123", provenance: "REAL" });
	t.show_bench = true;
	const bench = t.panels_html();
	check("the bench shows provenance", bench.includes("provenance"));
	check("the bench shows the raw native figures", bench.includes("39685"));
	check("the bench shows identity_source", bench.includes("identity_source"));
}

// ---------------------------------------------------------------------------
// 6. Big-number formatting.
// ---------------------------------------------------------------------------
{
	const t = terminal();
	// 12 ETH in wei exceeds 2^53; a Number-based formatter loses the tail.
	check(
		"native formatting survives wei-scale amounts",
		t.fmt_native("12000000000000000001").replace(/[^0-9]/g, "") === "12000000000000000001",
		t.fmt_native("12000000000000000001")
	);
}

// ---------------------------------------------------------------------------
// 7. Points: a disclosure, never a claim.
// ---------------------------------------------------------------------------
{
	const LOYALTY = (award, extra = {}) =>
		Object.assign(
			{
				reachable: true,
				facts: { redemption_rate: 100, per_issue_ceiling: 1000000, per_epoch_ceiling: 10000000 },
				earning_only:
					"EARNING ONLY. Points accrue and cannot be devalued. SPENDING THEM DOES NOT " +
					"WORK YET: a redemption needs the customer to co-sign. Do not tell a customer they can spend these.",
				ceilings: [
					["The rate is locked. Prices are not.", "100 points buy one cent, and that rate can never change."],
					["Points cannot be sold or transferred.", "Nothing can move them off your account."],
				],
				check_it_yourself: [["The contract itself", "https://ootle-indexer-a.tari.com/substates/component_x"]],
				award,
			},
			extra
		);

	const t = terminal();
	t.sale = sale({ state: "confirmed", end_kind: "clean" });

	// Pending: the degraded wording is the default.
	t.loyalty = LOYALTY({
		state: "pending",
		points: 10000,
		wording: "WOULD have earned 10,000 loyalty points. NOT ISSUED.",
		claims_points: false,
		reason: "",
	});
	let html = t.done_html();
	check("a pending award renders the degraded wording", html.includes("NOT ISSUED"));
	check("a pending award is not styled as held", !html.includes("cpos-award-held"));
	check("EARNING ONLY ships wherever points are mentioned", html.includes("EARNING ONLY"));
	check(
		"the operator is told not to claim spending",
		html.includes("Do not tell a customer they can spend these")
	);

	// Issued: only then may it read as held.
	t.loyalty = LOYALTY({
		state: "issued",
		points: 10000,
		wording: "HOLDS 10,000 loyalty points (100/cent at the time of sale)",
		claims_points: true,
		reason: "",
	});
	html = t.done_html();
	check("an issued award reads as held", html.includes("HOLDS") && html.includes("cpos-award-held"));
	check("EARNING ONLY still ships on an issued award", html.includes("EARNING ONLY"));

	// The ceilings are one click away, and real when opened.
	check("the limits are offered", html.includes('data-act="points"'));
	check("the limits are collapsed by default", !html.includes("cpos-ceilings"));
	t.show_points = true;
	html = t.done_html();
	check("opening the limits shows the locked rate", html.includes("can never change"));
	check("opening the limits shows soulboundness", html.includes("cannot be sold or transferred"));
	check("the customer is handed a URL to check", html.includes("ootle-indexer-a.tari.com"));

	// Unreachable: claim nothing at all.
	t.loyalty = { reachable: false, unreachable_because: "the indexer did not answer" };
	html = t.done_html();
	check(
		"an unreadable policy layer claims nothing about points",
		html.includes("nothing is claimed about points") &&
			!html.includes("HOLDS") &&
			!html.includes("WOULD")
	);

	// A sale with no loyalty at all must not grow a points block.
	t.loyalty = null;
	html = t.done_html();
	check("a sale with no policy tier renders no points block", !html.includes("cpos-points"));
}

// ---------------------------------------------------------------------------
console.log("");
PASS.forEach((line) => console.log(`  PASS  ${line}`));
FAIL.forEach((line) => console.log(`  FAIL  ${line}`));
console.log("");
console.log(`  ${PASS.length} passed, ${FAIL.length} failed`);
process.exit(FAIL.length ? 1 : 0);
