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
 * This file asserts on RENDERED OUTPUT only -- the HTML a state produces.
 * What that output does when it is clicked is a different question and is
 * answered in terminal_button_test.js, against the same stubbed desk.
 */

const { load, Reporter, sale } = require("./terminal_harness");

const report = new Reporter("terminal render");
const check = (rule, condition, detail = "") => report.check(rule, condition, detail);

const harness = load();
const Terminal = harness.Terminal;

// The rails call fires from the constructor. Answering it keeps a routing
// failure from raising a notice on every card this file renders.
harness.server.answer("cryptopos.api.rails", () => RAILS);

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
// ---------------------------------------------------------------------------
// 8. The values themselves, not merely their labels.
//
// Everything in this section exists because a mutation survived. The page is
// full of `x || ""` fallbacks, and swapping one to `x && ""` renders the
// FALLBACK in place of the value -- a screen that quietly shows nothing where
// the URI, the reference or the timestamp should be. Asserting that a block
// is present says nothing about that; asserting what is IN it does.
// ---------------------------------------------------------------------------
{
	const t = terminal();
	t.sale = sale();
	const html = t.awaiting_html();

	check(
		"the payment URI is rendered as the element's text",
		html.includes(">bitcoin:tb1qexample?amount=0.0004<"),
		"the title attribute alone satisfies a bare includes() while the line renders empty"
	);
	check("and again as its title, for when the line is truncated", html.includes('title="bitcoin:tb1qexample'));
	check(
		"the rate lock shows the time it expires",
		html.includes("20:15"),
		"slice(11, 16) of the timestamp -- one character either way is a different claim"
	);
	check("and does not spill the date or the seconds into it", !html.includes("20:15:"));

	// The provenance flags are their own block. `includes("testnet")` is not
	// enough on its own: the gate text says "testnet settles at 1", so that
	// check passed even with the mode flag suppressed entirely.
	const flags = html.slice(html.indexOf("cpos-flags"), html.indexOf("cpos-row"));
	check("a non-mainnet mode is flagged in the flags block", flags.includes("testnet"));
	check("a shared address is flagged in the flags block", flags.includes("shared address"));
	check(
		"a merchant-configured address raises no identity flag",
		!flags.includes("address not merchant-configured")
	);
	check("a REAL provenance is not flagged as simulated", !flags.includes("simulated"));
	check("a sale that has heard something does not say nothing has answered",
		!flags.includes("nothing has answered yet"));

	const simulated = terminal();
	simulated.sale = sale({ mode: "mainnet", provenance: "SIMULATED", identity_source: "derived", binding: "" });
	const simFlags = simulated.awaiting_html();
	check("a simulated sale says so", simFlags.includes("simulated"));
	check("an address the merchant did not configure says so", simFlags.includes("address not merchant-configured"));
	check("a mainnet sale raises no mode flag", !simFlags.includes(">mainnet<"));

	const silent = terminal();
	silent.sale = sale({ provenance: "" });
	check(
		"a sale nothing has answered about says exactly that",
		silent.awaiting_html().includes("nothing has answered yet")
	);
}

// ---------------------------------------------------------------------------
// 9. The QR is drawn from what the server sent, including its quiet zone.
// ---------------------------------------------------------------------------
{
	const t = terminal();

	t.sale = sale({ qr_modules: { size: 3, quiet: 2, rows: ["101", "010", "101"] } });
	check(
		"the quiet zone the server chose is the one drawn",
		t.awaiting_html().includes('viewBox="0 0 7 7"'),
		"3 modules plus 2 either side"
	);

	t.sale = sale({ qr_modules: { size: 3, rows: ["101", "010", "101"] } });
	check(
		"a grid with no quiet zone falls back to the spec's four",
		t.awaiting_html().includes('viewBox="0 0 11 11"'),
		"scanners fail intermittently without it, which reads as the customer's phone"
	);

	check("no modules at all draws nothing rather than a broken svg", t.qr_svg(null) === "");
	check("an empty grid draws nothing", t.qr_svg({ size: 0, quiet: 4 }) === "");
}

// ---------------------------------------------------------------------------
// 10. Formatting, asserted exactly.
//
// `includes("$42.00")` is satisfied by "$42.000". Equality is not.
// ---------------------------------------------------------------------------
{
	const t = terminal();
	check("a whole-dollar amount formats to two places", t.fmt_usd(4200) === "$42.00", t.fmt_usd(4200));
	check("a zero amount is zero, not a cent", t.fmt_usd(0) === "$0.00", t.fmt_usd(0));
	check("a missing amount is zero", t.fmt_usd(undefined) === "$0.00", t.fmt_usd(undefined));
	check("a single cent formats as one", t.fmt_usd(1) === "$0.01", t.fmt_usd(1));
	check(
		"a native amount that is not a number is shown as it arrived",
		t.fmt_native("not-a-number") === "not-a-number",
		t.fmt_native("not-a-number")
	);
	check("a missing native amount reads as zero", t.fmt_native(undefined) === "0", t.fmt_native(undefined));
}

// ---------------------------------------------------------------------------
// 11. The ending screen's own values.
// ---------------------------------------------------------------------------
{
	const t = terminal();

	t.sale = sale({ state: "confirmed", end_kind: "clean", sales_invoice: null, bookable: false });
	let html = t.done_html();
	check("an unbooked sale says why", html.includes("not settled (state is awaiting)"));
	check("the sale's own reference is printed", html.includes("INV-20260815-0001"));
	check("and the customer-facing ref beside it", html.includes("K7M2-P9QX-4TWN"));

	t.sale = sale({ state: "confirmed", end_kind: "over", credited_native: "1000000" });
	check("an overpayment names the unit both figures are in", t.done_html().includes("satoshi"));

	t.sale = sale({ state: "needs_review", end_kind: "unidentified", sighted_native: "50000" });
	check("sighted money names its unit too", t.done_html().includes("satoshi"));

	// The sighted block must appear only when something was actually sighted.
	t.sale = sale({ state: "expired", end_kind: "clean", sighted_native: "0" });
	check("a sale that sighted nothing renders no sighted block", !t.done_html().includes("cpos-sighted"));
	t.sale = sale({ state: "expired", end_kind: "clean", sighted_native: "" });
	check("nor does one with no sighted figure at all", !t.done_html().includes("cpos-sighted"));
	t.sale = sale({ state: "expired", end_kind: "under", sighted_native: "1" });
	check("a single sighted unit is still sighted", t.done_html().includes("cpos-sighted"));

	// An ending the table does not know still has to render something.
	// `state_word` deliberately unlike `state`: with the two the same, every
	// rung of the `state_word || state || ""` fallback renders the same text
	// and none of them is actually being tested.
	t.sale = sale({ state: "voided", state_word: "Written off", end_kind: "" });
	html = t.done_html();
	check("an unrecognised ending uses the sale's own word for itself", html.includes("WRITTEN OFF"));
	check("and not its raw state", !html.includes("VOIDED"));

	t.sale = sale({ state: "voided", state_word: "", end_kind: "" });
	check(
		"a sale with no word of its own falls back to its state",
		t.done_html().includes("VOIDED")
	);
}

// ---------------------------------------------------------------------------
// 12. The panels' contents.
// ---------------------------------------------------------------------------
{
	const t = terminal();
	t.sale = sale({
		state: "confirmed",
		end_kind: "clean",
		tx_id: "abc123",
		settled_at: "2026-08-15 20:11:07",
		events: [
			// Microseconds on purpose: Frappe writes them, and slice(11, 19)
			// must cut exactly at the seconds rather than one character past.
			{ at: "2026-08-15 20:04:31.482913", source: "scheduler", from_state: "awaiting", to_state: "detected", detail: "seen in mempool" },
		],
	});

	t.show_bench = true;
	let html = t.panels_html();
	check("the bench prints the transaction id", html.includes("abc123"));
	check("the bench prints the settlement time", html.includes("2026-08-15 20:11:07"));
	check("the bench prints the end kind", html.includes("clean"));
	check("the bench prints the binding", html.includes("shared"));
	check("the bench prints the provenance", html.includes("REAL"));

	t.show_bench = false;
	t.show_log = true;
	html = t.panels_html();
	check("the log prints the time of day only", html.includes("20:04:31"));
	check("and not the date with it", !html.includes("2026-08-15 20:04:31"));
	check("nor a stray fraction of a second", !html.includes("20:04:31."));
	check("the log names the transport that caused the transition", html.includes("scheduler"));
	check("the log prints the detail", html.includes("seen in mempool"));
	check("the log prints both states", html.includes("awaiting") && html.includes("detected"));

	// An empty field must read as a dash, not vanish.
	const bare = terminal();
	bare.sale = sale({ tx_id: "", settled_at: "", end_kind: "", binding: "", provenance: "" });
	bare.show_bench = true;
	check("a bench row with no value shows a dash", bare.panels_html().includes("&mdash;") ||
		bare.panels_html().includes("—"));
}

// ---------------------------------------------------------------------------
// 13. Points, in the shapes the server can actually send.
// ---------------------------------------------------------------------------
{
	const t = terminal();
	t.sale = sale({ state: "confirmed", end_kind: "clean" });

	const BASE = {
		reachable: true,
		facts: { redemption_rate: 100 },
		earning_only: "EARNING ONLY.",
		ceilings: [["head", "body"]],
		check_it_yourself: [["label", "https://example.invalid/x"]],
	};

	t.loyalty = Object.assign({}, BASE, { award: null });
	let html = t.done_html();
	check("a settled sale with no award record says so", html.includes("No award record exists"));
	check("and does not claim points are held", !html.includes("cpos-award-held"));

	t.loyalty = Object.assign({}, BASE, {
		award: { state: "refused", wording: "NOT ISSUED.", claims_points: false, reason: "over the per-epoch ceiling" },
	});
	check("a refused award shows the reason it was refused",
		t.done_html().includes("over the per-epoch ceiling"));

	// Reachable, and still unreadable: a different field carries the reason.
	t.loyalty = { reachable: true, facts: null, unreadable_because: "the state did not parse" };
	html = t.done_html();
	check("a reachable but unreadable policy layer claims nothing", html.includes("nothing is claimed about points"));
	check("and shows the reason it could not be read", html.includes("the state did not parse"));

	t.loyalty = { reachable: false, unreachable_because: "the indexer did not answer" };
	check("an unreachable one shows its own reason",
		t.done_html().includes("the indexer did not answer"));
}

report.report();
