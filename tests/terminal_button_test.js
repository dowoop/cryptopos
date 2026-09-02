/* Every control on the terminal, driven the way a cashier drives it.
 *
 *   node tests/terminal_button_test.js
 *
 * The render suite next door proves what each state LOOKS like. It cannot
 * prove that any of it does anything: it reads the HTML a method returned,
 * and a button whose handler was never attached renders exactly the same as
 * one that works. That gap was real — the old stub answered `find()` with a
 * no-op, so `wire()` ran against an object that discarded every handler, and
 * the whole event layer of the page was untested while the suite was green.
 *
 * So nothing here calls a render method directly. Every check goes through a
 * click, a change, or a keypress on an element parsed out of the HTML the
 * page actually put on screen, and asserts on what changed afterwards —
 * state, the next render, or the call that went to the server.
 *
 * The inventory is deliberate. There are eleven distinct controls and a
 * keyboard, and every one of them appears below at least once:
 *
 *     twelve keypad keys      digits, decimal point, backspace
 *     the rail <select>       chooses what the sale is denominated in
 *     Charge                  the one button that spends money
 *     Poll the node           single-steps the heartbeat
 *     auto-poll               starts and stops the interval
 *     Cancel                  abandons a live sale
 *     New sale                leaves an ended one
 *     the notice's ×          dismisses a held refusal
 *     what are the limits?    expands the loyalty ceilings
 *     dev bench               opt-in developer panel
 *     activity log            opt-in, and clears a held refusal
 */

const {
	load,
	Reporter,
	click,
	setChecked,
	choose,
	press,
	settle,
	find,
	findAll,
	exists,
	serverRefusal,
	RAILS,
	sale,
} = require("./terminal_harness");

const report = new Reporter("terminal buttons");
const check = (rule, condition, detail = "") => report.check(rule, condition, detail);

const CONFIRMED = {
	state: "confirmed",
	end_kind: "clean",
	credited_native: "39685",
	sales_invoice: "ACC-SINV-0001",
	bookable: true,
};

const LOYALTY = {
	reachable: true,
	facts: { redemption_rate: 100, per_issue_ceiling: 1000000, per_epoch_ceiling: 10000000 },
	earning_only: "EARNING ONLY. Points accrue and cannot be devalued.",
	ceilings: [
		["The rate is locked. Prices are not.", "100 points buy one cent, and that rate can never change."],
	],
	check_it_yourself: [["The contract itself", "https://ootle-indexer-a.tari.com/substates/component_x"]],
	award: {
		state: "issued",
		points: 10000,
		wording: "HOLDS 10,000 loyalty points",
		claims_points: true,
		reason: "",
	},
};

/** A terminal whose rails have loaded and whose first render has happened. */
async function booted(routes = {}) {
	const harness = load();
	harness.server.answer("cryptopos.api.rails", () => RAILS);
	Object.entries(routes).forEach(([method, responder]) => harness.server.answer(method, responder));
	const terminal = new harness.Terminal({ body: {} });
	await settle();
	return { harness, terminal };
}

const htmlOf = (harness, terminal) => harness.$(terminal.page.body).html();

/** Key an amount through the keypad buttons, one click per character. */
function keyIn(terminal, text) {
	[...text].forEach((character) => click(terminal, `[data-key="${character}"]`));
}

async function main() {
	// -----------------------------------------------------------------
	// 1. The twelve keys.
	// -----------------------------------------------------------------
	{
		const { harness, terminal } = await booted();

		const keys = findAll(terminal, "[data-key]");
		check("the keypad renders twelve keys", keys.length === 12, String(keys.length));
		check(
			"every key was wired by the page's own wire()",
			keys.every((key) => (key.handlers.click || []).length === 1),
			"a key with no handler renders identically to one that works"
		);

		keyIn(terminal, "123456789");
		check("clicking the nine digit keys builds the amount", terminal.amount === "123456789", terminal.amount);

		click(terminal, '[data-key="0"]');
		check(
			"a tenth digit is refused rather than silently dropped later",
			terminal.amount === "123456789",
			terminal.amount
		);

		click(terminal, '[data-key="back"]');
		check("the backspace key removes one digit", terminal.amount === "12345678", terminal.amount);

		const zeroed = await booted();
		click(zeroed.terminal, '[data-key="."]');
		check(
			"a leading decimal point becomes 0. rather than a bare dot",
			zeroed.terminal.amount === "0.",
			zeroed.terminal.amount
		);
		keyIn(zeroed.terminal, "50");
		check("cents key in after the point", zeroed.terminal.cents() === 50, String(zeroed.terminal.cents()));
		click(zeroed.terminal, '[data-key="."]');
		check(
			"a second decimal point is refused",
			zeroed.terminal.amount === "0.50",
			zeroed.terminal.amount
		);
		click(zeroed.terminal, '[data-key="9"]');
		check(
			"a third decimal place is refused at the button",
			zeroed.terminal.cents() === 50,
			zeroed.terminal.amount
		);

		check(
			"the amount on screen tracks the keys pressed",
			htmlOf(zeroed.harness, zeroed.terminal).includes("0.50")
		);
	}

	// -----------------------------------------------------------------
	// 2. The rail select.
	// -----------------------------------------------------------------
	{
		const { harness, terminal } = await booted();
		check("the first rail is selected without being chosen", terminal.rail === "btc", terminal.rail);
		check(
			"the selected rail's gate is on the charge surface",
			htmlOf(harness, terminal).includes("confs &gt;= 3")
		);

		choose(terminal, ".cpos-rail", "eth");
		check("choosing a rail changes what the sale will be charged in", terminal.rail === "eth", terminal.rail);
		check(
			"choosing a rail re-renders with THAT rail's gate",
			htmlOf(harness, terminal).includes("confs &gt;= 12"),
			"a stale gate would promise the wrong number of confirmations"
		);
		check(
			"a rail that 'works' shows no maturity warning",
			!htmlOf(harness, terminal).includes("cpos-maturity")
		);

		choose(terminal, ".cpos-rail", "btc");
		check(
			"choosing a 'partial' rail brings the warning back",
			htmlOf(harness, terminal).includes("shared configured address")
		);
	}

	// -----------------------------------------------------------------
	// 3. Charge — the one button that spends money.
	// -----------------------------------------------------------------
	{
		const { harness, terminal } = await booted({
			"cryptopos.api.charge": (args) => sale({ usd_cents: args.usd_cents }),
		});

		check("charge is disabled before an amount is keyed", find(terminal, '[data-act="charge"]').disabled);
		click(terminal, '[data-act="charge"]');
		check(
			"clicking a disabled charge sends nothing to the server",
			harness.server.callsTo("cryptopos.api.charge").length === 0,
			"the guard is in charge() itself, not only in the disabled attribute"
		);

		keyIn(terminal, "42");
		check("charge enables once there is an amount", !find(terminal, '[data-act="charge"]').disabled);

		click(terminal, '[data-act="charge"]');
		const posted = harness.server.callsTo("cryptopos.api.charge");
		check("clicking charge calls the charge endpoint once", posted.length === 1, String(posted.length));
		// Read defensively: if the button is not wired at all, every check
		// below is a failure worth SEEING, and indexing into an empty array
		// would abandon the run with a stack trace instead.
		const args = (posted[0] || {}).args || {};
		check("it posts the keyed amount in cents", args.usd_cents === 4200, String(args.usd_cents));
		check("it posts the selected rail", args.rail_key === "btc", String(args.rail_key));

		check(
			"while the charge is in flight the button says so",
			find(terminal, '[data-act="charge"]').text.includes("Charging"),
			find(terminal, '[data-act="charge"]').text
		);
		check(
			"and cannot be pressed a second time",
			find(terminal, '[data-act="charge"]').disabled,
			"a double press is a double sale"
		);

		await settle();
		check("the answered sale replaces the keypad with the awaiting screen", exists(terminal, '[data-act="poll"]'));
		check("the QR is drawn from the modules the server sent", htmlOf(harness, terminal).includes("<svg"));
		check("the keyed amount is cleared for the next sale", terminal.amount === "", terminal.amount);
	}

	// -----------------------------------------------------------------
	// 4. A refused charge is a refusal, and must reach the screen.
	// -----------------------------------------------------------------
	{
		const { harness, terminal } = await booted({
			"cryptopos.api.charge": () => serverRefusal("no receiving address is configured for btc"),
		});

		keyIn(terminal, "42");
		click(terminal, '[data-act="charge"]');
		await settle();

		check(
			"a refused charge puts the server's own words on the terminal",
			htmlOf(harness, terminal).includes("no receiving address is configured for btc"),
			"the reason is unwrapped from _server_messages, not replaced with a generic one"
		);
		check("the terminal is usable again after a refusal", !terminal.busy);
		check("no sale was started", terminal.sale === null);
		check("the keyed amount survives so it need not be re-entered", terminal.amount === "42", terminal.amount);

		click(terminal, '[data-act="dismiss"]');
		check(
			"the notice's × dismisses it",
			!htmlOf(harness, terminal).includes("no receiving address is configured")
		);
		check("dismissing leaves the keypad usable", exists(terminal, '[data-key="1"]'));
	}

	// -----------------------------------------------------------------
	// 5. Poll the node.
	// -----------------------------------------------------------------
	{
		let answer = sale();
		const { harness, terminal } = await booted({
			"cryptopos.api.charge": () => sale(),
			"cryptopos.api.poll": () => answer,
			"cryptopos.api.loyalty_status": () => LOYALTY,
		});

		keyIn(terminal, "42");
		click(terminal, '[data-act="charge"]');
		await settle();

		answer = sale({ state: "detected" });
		click(terminal, '[data-act="poll"]');
		await settle();
		const polls = harness.server.callsTo("cryptopos.api.poll");
		check("clicking poll single-steps the heartbeat", polls.length === 1, String(polls.length));
		check("it names the sale it is polling", polls[0].args.sale_name === "CPS-2026-00001");
		check("a mempool sighting is shown as such", htmlOf(harness, terminal).includes("Seen, not yet mined"));
		check("the sale is still live, so poll is still offered", exists(terminal, '[data-act="poll"]'));

		answer = sale(CONFIRMED);
		click(terminal, '[data-act="poll"]');
		await settle();
		check("a settled sale ends on the done screen", htmlOf(harness, terminal).includes("SETTLED"));
		check("the done screen offers a new sale, not another poll", !exists(terminal, '[data-act="poll"]'));
		check(
			"reaching an ending loads the policy tier",
			harness.server.callsTo("cryptopos.api.loyalty_status").length === 1
		);
		check("the award is disclosed once it is known", htmlOf(harness, terminal).includes("HOLDS 10,000"));
	}

	// -----------------------------------------------------------------
	// 6. A poll that cannot reach the server.
	// -----------------------------------------------------------------
	{
		const { harness, terminal } = await booted({
			"cryptopos.api.charge": () => sale(),
			"cryptopos.api.poll": () => serverRefusal("the node did not answer"),
		});

		keyIn(terminal, "42");
		click(terminal, '[data-act="charge"]');
		await settle();

		click(terminal, '[data-act="poll"]');
		await settle();
		check(
			"a poll that fails says so rather than looking like nothing happened",
			htmlOf(harness, terminal).includes("the node did not answer")
		);
		check("the sale is left alone rather than guessed at", terminal.sale.state === "awaiting");
	}

	// -----------------------------------------------------------------
	// 7. Auto-poll: the checkbox that starts and stops the clock.
	// -----------------------------------------------------------------
	{
		let answer = sale();
		const { harness, terminal } = await booted({
			"cryptopos.api.charge": () => sale(),
			"cryptopos.api.poll": () => answer,
			"cryptopos.api.loyalty_status": () => LOYALTY,
		});

		keyIn(terminal, "42");
		click(terminal, '[data-act="charge"]');
		await settle();

		check("no timer runs until auto-poll is asked for", harness.timers.active.size === 0);

		setChecked(terminal, '[data-act="autopoll"]', true);
		check("ticking auto-poll starts exactly one interval", harness.timers.active.size === 1);
		check(
			"the interval is the documented ten seconds",
			[...harness.timers.active.values()][0].ms === 10000,
			String([...harness.timers.active.values()][0].ms)
		);

		harness.timers.tick();
		await settle();
		check("a tick polls without being clicked", harness.server.callsTo("cryptopos.api.poll").length === 1);

		setChecked(terminal, '[data-act="autopoll"]', false);
		check("unticking stops the interval", harness.timers.active.size === 0);
		harness.timers.tick();
		await settle();
		check(
			"a stopped interval does not keep polling",
			harness.server.callsTo("cryptopos.api.poll").length === 1
		);

		// Restart, and let the sale end on a tick rather than a click.
		setChecked(terminal, '[data-act="autopoll"]', true);
		answer = sale(CONFIRMED);
		harness.timers.tick();
		await settle();
		check("a sale that ends on a tick stops its own timer", harness.timers.active.size === 0);
		check("and lands on the done screen", htmlOf(harness, terminal).includes("SETTLED"));
	}

	// -----------------------------------------------------------------
	// 8. Cancel, and New sale.
	// -----------------------------------------------------------------
	{
		const { harness, terminal } = await booted({
			"cryptopos.api.charge": () => sale(),
			"cryptopos.api.poll": () => sale(CONFIRMED),
			"cryptopos.api.loyalty_status": () => LOYALTY,
		});

		keyIn(terminal, "42");
		click(terminal, '[data-act="charge"]');
		await settle();
		setChecked(terminal, '[data-act="autopoll"]', true);
		check("a live sale with auto-poll on has a timer", harness.timers.active.size === 1);

		click(terminal, '[data-act="cancel"]');
		check("Cancel abandons the sale", terminal.sale === null);
		check("Cancel stops the timer it would otherwise leave running", harness.timers.active.size === 0);
		check("Cancel returns the keypad", exists(terminal, '[data-key="1"]'));
		check("Cancel clears the amount", terminal.amount === "", terminal.amount);

		// Now the same control under its other name, on the done screen.
		keyIn(terminal, "42");
		click(terminal, '[data-act="charge"]');
		await settle();
		click(terminal, '[data-act="poll"]');
		await settle();
		check("the ended sale's button reads New sale", find(terminal, '[data-act="cancel"]').text.includes("New sale"));

		click(terminal, '[data-act="cancel"]');
		check("New sale returns the keypad", exists(terminal, '[data-key="1"]'));
		check("New sale forgets the previous sale's award", terminal.loyalty === null);
		check("New sale collapses the limits again", terminal.show_points === false);
	}

	// -----------------------------------------------------------------
	// 9. The limits link.
	// -----------------------------------------------------------------
	{
		const { harness, terminal } = await booted({
			"cryptopos.api.charge": () => sale(),
			"cryptopos.api.poll": () => sale(CONFIRMED),
			"cryptopos.api.loyalty_status": () => LOYALTY,
		});

		keyIn(terminal, "42");
		click(terminal, '[data-act="charge"]');
		await settle();
		click(terminal, '[data-act="poll"]');
		await settle();

		check("the limits are collapsed until asked for", !htmlOf(harness, terminal).includes("cpos-ceilings"));
		check("EARNING ONLY ships without being asked for", htmlOf(harness, terminal).includes("EARNING ONLY"));

		click(terminal, '[data-act="points"]');
		check("the link expands the ceilings", htmlOf(harness, terminal).includes("can never change"));
		check(
			"and hands the customer a URL to check for themselves",
			htmlOf(harness, terminal).includes("ootle-indexer-a.tari.com")
		);
		check("the link now offers to hide them", find(terminal, '[data-act="points"]').text.includes("hide"));

		click(terminal, '[data-act="points"]');
		check("clicking again collapses them", !htmlOf(harness, terminal).includes("cpos-ceilings"));
	}

	// -----------------------------------------------------------------
	// 10. The two developer toggles.
	// -----------------------------------------------------------------
	{
		const { harness, terminal } = await booted({
			"cryptopos.api.charge": () => sale({ events: [{ at: "2026-08-15 20:00:01", source: "api", from_state: "idle", to_state: "awaiting", detail: "charged" }] }),
		});

		check("the dev bench is off on first open", !find(terminal, '[data-act="bench"]').checked);
		check("the activity log is off on first open", !find(terminal, '[data-act="log"]').checked);

		keyIn(terminal, "42");
		click(terminal, '[data-act="charge"]');
		await settle();

		setChecked(terminal, '[data-act="bench"]', true);
		check("ticking the bench shows the raw sale", htmlOf(harness, terminal).includes("identity_source"));
		check("the bench carries the native figures unrounded", htmlOf(harness, terminal).includes("39685"));

		setChecked(terminal, '[data-act="bench"]', false);
		check("unticking the bench hides it again", !htmlOf(harness, terminal).includes("identity_source"));

		setChecked(terminal, '[data-act="log"]', true);
		check("ticking the log shows the transitions", htmlOf(harness, terminal).includes("awaiting"));
		check("each row is attributed to what caused it", htmlOf(harness, terminal).includes("api"));
	}

	// -----------------------------------------------------------------
	// 11. The rule that makes hiding the log allowable.
	//
	// A disclosure may hide an EXPLANATION and never a REFUSAL. With the log
	// closed a refusal is held and painted on the card; opening the log is
	// what clears it. Both halves proved through the controls themselves.
	// -----------------------------------------------------------------
	{
		const { harness, terminal } = await booted({
			"cryptopos.api.charge": () => serverRefusal("rail btc is switched off"),
		});

		keyIn(terminal, "42");
		click(terminal, '[data-act="charge"]');
		await settle();
		check("with the log closed the refusal is painted on the card", exists(terminal, '[data-act="dismiss"]'));

		setChecked(terminal, '[data-act="log"]', true);
		check("opening the log is what clears the held refusal", !exists(terminal, '[data-act="dismiss"]'));
		check("the refusal is not simply gone -- the log is now open", terminal.show_log === true);

		// And with the log already open, nothing is held in the first place.
		const second = await booted({
			"cryptopos.api.charge": () => serverRefusal("rail btc is switched off"),
		});
		setChecked(second.terminal, '[data-act="log"]', true);
		keyIn(second.terminal, "42");
		click(second.terminal, '[data-act="charge"]');
		await settle();
		check(
			"with the log open the refusal is not duplicated onto the card",
			!exists(second.terminal, '[data-act="dismiss"]')
		);
	}

	// -----------------------------------------------------------------
	// 12. The real keyboard, which is how a cashier actually works.
	// -----------------------------------------------------------------
	{
		const { harness, terminal } = await booted({
			"cryptopos.api.charge": () => sale(),
			"cryptopos.api.poll": () => sale(),
			"cryptopos.api.loyalty_status": () => LOYALTY,
		});

		check("typing a digit keys it", press(harness, "4") && terminal.amount === "4", terminal.amount);
		press(harness, "2");
		press(harness, ".");
		press(harness, "5");
		check("typing a decimal amount works", terminal.cents() === 4250, String(terminal.cents()));
		press(harness, "Backspace");
		check("Backspace removes a character", terminal.amount === "42.", terminal.amount);

		check(
			"an unhandled key is left to the browser",
			press(harness, "F5") === false,
			"swallowing every keypress would break refresh, find and tab"
		);

		const input = find(terminal, '[data-act="bench"]');
		press(harness, "7", input);
		check(
			"a keypress inside a form control is not stolen by the keypad",
			terminal.amount === "42.",
			terminal.amount
		);

		press(harness, "Escape");
		check("Escape with no sale clears the amount", terminal.amount === "", terminal.amount);

		press(harness, "5");
		press(harness, "0");
		press(harness, "Enter");
		await settle();
		check("Enter with no sale charges", harness.server.callsTo("cryptopos.api.charge").length === 1);
		check("and lands on the awaiting screen", exists(terminal, '[data-act="poll"]'));

		press(harness, "Enter");
		await settle();
		check("Enter on a live sale polls", harness.server.callsTo("cryptopos.api.poll").length === 1);

		press(harness, "Escape");
		check("Escape on a live sale abandons it", terminal.sale === null);

		// Enter on an ENDED sale starts the next one rather than polling a
		// sale that can no longer change.
		press(harness, "5");
		press(harness, "0");
		press(harness, "Enter");
		await settle();
		harness.server.answer("cryptopos.api.poll", () => sale(CONFIRMED));
		press(harness, "Enter");
		await settle();
		check("the sale has ended", terminal.sale.state === "confirmed");
		const pollsBefore = harness.server.callsTo("cryptopos.api.poll").length;
		press(harness, "Enter");
		check("Enter on an ended sale starts a new one", terminal.sale === null);
		check(
			"and does not poll a sale that can no longer change",
			harness.server.callsTo("cryptopos.api.poll").length === pollsBefore
		);
	}

	// -----------------------------------------------------------------
	// 13. Handlers survive every re-render.
	//
	// The page rebuilds its whole body on each render, so every handler from
	// the previous one is discarded. `wire()` running again is the only
	// reason anything still works on the second screen — and a button that
	// works once and then goes dead is the hardest kind of defect to see.
	// -----------------------------------------------------------------
	{
		const { harness, terminal } = await booted({
			"cryptopos.api.charge": () => sale(),
			"cryptopos.api.poll": () => sale(CONFIRMED),
			"cryptopos.api.loyalty_status": () => LOYALTY,
		});

		for (let round = 1; round <= 3; round++) {
			keyIn(terminal, "42");
			click(terminal, '[data-act="charge"]');
			await settle();
			click(terminal, '[data-act="poll"]');
			await settle();
			click(terminal, '[data-act="cancel"]');
		}
		check("three full sales run entirely through the buttons", terminal.sale === null);
		check(
			"the keypad still responds after nine renders",
			(click(terminal, '[data-key="7"]'), terminal.amount === "7"),
			terminal.amount
		);
		check(
			"and the charge button is still wired",
			(find(terminal, '[data-act="charge"]').handlers.click || []).length === 1
		);
	}

	// -----------------------------------------------------------------
	// 14. The rails call failing is itself a refusal.
	// -----------------------------------------------------------------
	{
		const harness = load();
		harness.server.answer("cryptopos.api.rails", () => serverRefusal("rails are not configured"));
		const terminal = new harness.Terminal({ body: {} });
		await settle();

		check(
			"a terminal that could not load its rails says so",
			htmlOf(harness, terminal).includes("could not load rails")
		);
		check("and still renders a keypad rather than an empty page", exists(terminal, '[data-key="1"]'));
		check(
			"charging with no rail is refused before it reaches the server",
			(click(terminal, '[data-key="5"]'),
			click(terminal, '[data-act="charge"]'),
			harness.server.callsTo("cryptopos.api.charge").length === 0)
		);
	}

	// -----------------------------------------------------------------
	// 15. The entry point the desk actually calls.
	//
	// Every section above constructs the class directly, which is convenient
	// and skips the one function Frappe itself invokes. A page that threw in
	// `on_page_load` would leave the terminal blank in the desk while every
	// other check here stayed green.
	// -----------------------------------------------------------------
	{
		const harness = load();
		harness.server.answer("cryptopos.api.rails", () => RAILS);

		const asked = [];
		const madeAppPage = harness.sandbox.frappe.ui.make_app_page;
		harness.sandbox.frappe.ui.make_app_page = (options) => {
			asked.push(options);
			return madeAppPage(options);
		};

		const wrapper = {};
		harness.sandbox.frappe.pages["terminal"].on_page_load(wrapper);
		await settle();

		check("the desk's entry point builds a page", asked.length === 1);
		check("it is titled Terminal", asked[0] && asked[0].title === "Terminal", asked[0] && asked[0].title);
		check(
			"it asks for a single column, which is what the keypad layout assumes",
			asked[0] && asked[0].single_column === true
		);
		check("it hangs the page off the wrapper the desk handed it", asked[0] && asked[0].parent === wrapper);

		const html = harness.$(wrapper).html();
		check("opening the page renders the keypad", html.includes('data-key="1"'));
		check("and offers the charge button", html.includes('data-act="charge"'));
	}

	// -----------------------------------------------------------------
	// 16. Behaviour that no assertion was defending.
	//
	// Each check below was written because a mutation to the page survived:
	// the code was made wrong and both suites stayed green. They are grouped
	// here rather than scattered because they share a cause -- they are the
	// quiet paths, the ones a cashier only meets when something is already
	// unusual.
	// -----------------------------------------------------------------
	{
		// A rails call that SUCCEEDS and returns nothing is not the same as
		// one that fails, and it must not be turned into a crash by reaching
		// for the first rail of an empty list.
		const { harness, terminal } = await booted({ "cryptopos.api.rails": () => [] });
		check("an empty rail list is not an error", !exists(terminal, '[data-act="dismiss"]'));
		check("the terminal still renders its keypad", exists(terminal, '[data-key="1"]'));
		check("the rail select says there are none", htmlOf(harness, terminal).includes("no rails configured"));
		check("and is disabled rather than empty", find(terminal, ".cpos-rail").disabled);
		check("no rail is selected", terminal.rail === null);

		click(terminal, '[data-key="5"]');
		click(terminal, '[data-act="charge"]');
		check(
			"charging with no rail is refused before the server is asked",
			harness.server.callsTo("cryptopos.api.charge").length === 0
		);
		check("and says so on the terminal", htmlOf(harness, terminal).includes("no rail selected"));
	}

	{
		// The rails call has its own error path, and it reports `e.message`
		// rather than the error object -- a card that reads "Error: ..." is a
		// stack trace pointed at a customer.
		const harness = load();
		harness.server.answer("cryptopos.api.rails", () => new Error("connection reset"));
		const terminal = new harness.Terminal({ body: {} });
		await settle();
		const html = harness.$(terminal.page.body).html();
		check("a failed rails load names the reason", html.includes("could not load rails: connection reset"));
		check("and does not stringify the error object", !html.includes("Error: connection reset"));
	}

	{
		// With rails, the select must carry the rails -- not the placeholder.
		const { harness, terminal } = await booted();
		const options = findAll(terminal, "option");
		check("every rail is offered", options.length === 2, String(options.length));
		check(
			"the current rail is the selected option",
			options.filter((o) => "selected" in o.attrs).map((o) => o.attrs.value).join() === "btc"
		);
		choose(terminal, ".cpos-rail", "eth");
		const after = findAll(terminal, "option");
		check(
			"choosing another rail moves the selection to it",
			after.filter((o) => "selected" in o.attrs).map((o) => o.attrs.value).join() === "eth"
		);
	}

	{
		// The backspace key is a key like the others and is drawn unlike them.
		const { terminal } = await booted();
		const back = find(terminal, '[data-key="back"]');
		const five = find(terminal, '[data-key="5"]');
		check("the backspace key carries its own class", (back.attrs.class || "").includes("cpos-key-back"));
		check("a digit key does not", !(five.attrs.class || "").includes("cpos-key-back"));
		check("the backspace key draws the erase glyph", back.text.includes("&#9003;"));
		check("a digit key draws its digit", five.text.trim() === "5");
	}

	{
		// One cent is an amount. `> 0` and `> 1` differ by exactly this sale.
		const { terminal } = await booted({ "cryptopos.api.charge": () => sale() });
		keyIn(terminal, "0.01");
		check("a one-cent sale is chargeable", terminal.cents() === 1, String(terminal.cents()));
		check("and the button says so", !find(terminal, '[data-act="charge"]').disabled);
	}

	{
		// The keypad is deaf during a live sale and listening once it ends,
		// because the next thing a cashier does after an ending is key the
		// next amount.
		const { harness, terminal } = await booted({
			"cryptopos.api.charge": () => sale(),
			"cryptopos.api.poll": () => sale(CONFIRMED),
			"cryptopos.api.loyalty_status": () => LOYALTY,
		});
		keyIn(terminal, "42");
		click(terminal, '[data-act="charge"]');
		await settle();

		press(harness, "5");
		check("a digit typed during a live sale is ignored", terminal.amount === "", terminal.amount);

		click(terminal, '[data-act="poll"]');
		await settle();
		press(harness, "5");
		check("a digit typed after the sale ends starts the next one", terminal.amount === "5", terminal.amount);
	}

	{
		// A background poll must not put a notice on the counter. The whole
		// point of the timer is that nobody is looking at it.
		const { harness, terminal } = await booted({
			"cryptopos.api.charge": () => sale(),
			"cryptopos.api.poll": () => serverRefusal("the node did not answer"),
		});
		keyIn(terminal, "42");
		click(terminal, '[data-act="charge"]');
		await settle();
		setChecked(terminal, '[data-act="autopoll"]', true);

		harness.timers.tick();
		await settle();
		check("a failed auto-poll raises no notice", !exists(terminal, '[data-act="dismiss"]'));
		check("it did reach the server", harness.server.callsTo("cryptopos.api.poll").length === 1);

		// The same failure, asked for by hand, IS reported.
		click(terminal, '[data-act="poll"]');
		await settle();
		check("the same failure clicked by hand is reported", exists(terminal, '[data-act="dismiss"]'));
	}

	{
		// A rejection that is not a Frappe refusal still has to say something
		// useful, and `e.message` is what that is.
		const { harness, terminal } = await booted({
			"cryptopos.api.charge": () => new Error("connection reset"),
		});
		keyIn(terminal, "42");
		click(terminal, '[data-act="charge"]');
		await settle();
		const html = htmlOf(harness, terminal);
		check("a plain error is reported by its message", html.includes("connection reset"));
		check(
			"and not by stringifying the error object",
			!html.includes("Error: connection reset"),
			"a customer-facing card should not read like a stack trace"
		);
	}

	{
		// A terminal left busy is a terminal whose charge button never
		// re-enables, and the sale that reveals it is the SECOND one.
		const { terminal } = await booted({
			"cryptopos.api.charge": () => sale(),
			"cryptopos.api.poll": () => sale(CONFIRMED),
			"cryptopos.api.loyalty_status": () => LOYALTY,
		});
		keyIn(terminal, "42");
		click(terminal, '[data-act="charge"]');
		await settle();
		check("the terminal is not left busy after a charge lands", terminal.busy === false);

		click(terminal, '[data-act="poll"]');
		await settle();
		click(terminal, '[data-act="cancel"]');
		keyIn(terminal, "42");
		check(
			"so the next sale can be charged",
			!find(terminal, '[data-act="charge"]').disabled,
			"busy left true would disable the button for the rest of the shift"
		);
	}

	// -----------------------------------------------------------------
	// 13. Cover this charge — the button every visitor pays with.
	//
	// It shipped with no test of any kind, which `make prove` reported as
	// two never-reached parts (`cover` and `[data-act="cover"]`) and which
	// nothing else could have caught: the render suite next door proves the
	// button is DRAWN, and a button whose handler was never wired renders
	// exactly the same as one that works. On a public instance with no
	// wallet in the visitor's hands this control IS the demo, so it is the
	// last one that should have been going untested.
	// -----------------------------------------------------------------
	{
		// `cover_html` gates on the rail: only `xtr` has a payer that is not
		// the customer's own wallet, so the fixture has to say so.
		const XTR = { rail_key: "xtr", unit_name: "microTari", invoiced_native: "5000000" };
		let answer = sale(XTR);
		const { harness, terminal } = await booted({
			"cryptopos.api.charge": () => sale(XTR),
			"cryptopos.api.poll": () => answer,
			"cryptopos.api.request_cover": () => ({
				name: "CPS-2026-00001",
				demo_cover_state: "requested",
				queued: true,
			}),
			"cryptopos.api.loyalty_status": () => LOYALTY,
		});

		keyIn(terminal, "42");
		click(terminal, '[data-act="charge"]');
		await settle();
		check("an xtr sale offers to be covered", exists(terminal, '[data-act="cover"]'));

		click(terminal, '[data-act="cover"]');
		await settle();
		const asks = harness.server.callsTo("cryptopos.api.request_cover");
		check("pressing it asks the house exactly once", asks.length === 1, String(asks.length));
		check("and it names the sale it is asking about", asks[0].args.sale_name === "CPS-2026-00001");
		check(
			"the visitor is told the house was asked, not that it paid",
			htmlOf(harness, terminal).includes("Asked the house to cover this")
		);
		check("the offer is withdrawn once asked", !exists(terminal, '[data-act="cover"]'));
		check(
			"asking starts the watch, so nobody has to press Poll",
			terminal.autopoll === true
		);

		// A REFUSAL HAS TO REACH THE VISITOR, VERBATIM. This is the whole
		// reason `demo_cover_note` exists: without the reason on screen, a
		// sale the house declined is indistinguishable from one nobody has
		// looked at, and the visitor watches it expire in silence.
		answer = sale({
			...XTR,
			demo_cover_state: "refused",
			demo_cover_note: "the demo wallet holds 254,759,987 uT and this needs 1,000,000,000",
		});
		click(terminal, '[data-act="poll"]');
		await settle();
		const refusedHtml = htmlOf(harness, terminal);
		check("a refusal says the house did not cover it", refusedHtml.includes("The house did not cover this"));
		check(
			"and carries the reason word for word",
			refusedHtml.includes("the demo wallet holds 254,759,987 uT and this needs 1,000,000,000")
		);
	}

	// -----------------------------------------------------------------
	// 14. The other side of that predicate: a rail the house cannot pay.
	// -----------------------------------------------------------------
	{
		const { harness, terminal } = await booted({
			"cryptopos.api.charge": () => sale({ rail_key: "btc" }),
			"cryptopos.api.poll": () => sale({ rail_key: "btc" }),
		});
		keyIn(terminal, "42");
		click(terminal, '[data-act="charge"]');
		await settle();
		check(
			"a btc sale offers no cover button -- the demo payer settles xtr only",
			!exists(terminal, '[data-act="cover"]')
		);
		check(
			"and it does not offer the house's help in words either",
			!htmlOf(harness, terminal).includes("Cover this charge")
		);
	}

	report.report();
}

main().catch((error) => {
	// A thrown error is still a result: print what did pass, then the throw,
	// so a broken control shows up as a named failure rather than only as a
	// stack trace from wherever the run happened to give up.
	report.check(`the suite ran to completion (${error.message})`, false);
	report.report();
});
