/* CryptoPoS terminal — the keypad IS the home.
 *
 * No gallery step and no product catalog in the charge path: the first thing
 * on screen is the amount field and the digits, because the overwhelmingly
 * common thing a cashier does is key a number and take money for it.
 *
 * Three audiences share one window, and the default belongs to exactly one
 * of them. The merchant gets the terminal card alone. The customer gets the
 * awaiting screen and its QR, leaned over the counter. The developer gets
 * two checkboxes -- the dev bench and the activity log -- both opt-in and
 * both off on first open.
 *
 * Hiding the log is allowable only because a disclosure may hide an
 * explanation and never a refusal. An error written to a hidden panel raises
 * a notice on the terminal itself; see note_error().
 */

frappe.pages["terminal"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Terminal"),
		single_column: true,
	});
	new CryptoPosTerminal(page);
};

const POLL_SECONDS = 10;

// The rails a non-operator may choose. See `offerable()` for why this exists
// and why a constant here is the short version of a setting.
const PUBLIC_RAILS = ["xtr"];

// Rails the house can pay for a visitor. Must agree with
// `cryptopos.api.COVERABLE_RAILS`; the server is the one that enforces it.
const COVERABLE_RAILS = ["xtr"];

class CryptoPosTerminal {
	constructor(page) {
		this.page = page;
		this.$body = $(page.body);
		this.amount = "";
		this.rail = null;
		this.rails = [];
		this.sale = null;
		this.autopoll = false;
		this.show_bench = false;
		this.show_log = false;
		this.loyalty = null;
		this.show_points = false;
		this.unseen_error = null;
		this.timer = null;

		this.inject_styles();
		this.bind_keyboard();
		this.load_rails();
	}

	// ------------------------------------------------------------------
	// Data
	// ------------------------------------------------------------------
	load_rails() {
		frappe
			.call({ method: "cryptopos.api.rails" })
			.then((r) => {
				this.rails = this.offerable(r.message || []);
				if (this.rails.length && !this.rail) {
					this.rail = this.preferred_rail();
				}
				this.render();
			})
			.catch((e) => {
				this.note_error(`could not load rails: ${e.message || e}`);
				this.render();
			});
	}

	// ------------------------------------------------------------------
	// WHICH RAILS A GIVEN PERSON MAY PICK
	//
	// THIS DEPLOYMENT ENABLES SEVEN RAILS AND CAN COMPLETE A VISITOR'S SALE ON
	// EXACTLY ONE. `xtr` is the only rail offered publicly (the decision is in
	// CONTINUE.md, 2026-08-31) and the only one the demo payer settles. The
	// other six receive at an address whose wallet is the customer's own, so a
	// visitor who picks one gets a QR nobody can pay, no error, and a sale that
	// expires -- measured on this instance: a guest charged $1.00 on `btc` and
	// it sat `awaiting` until it lapsed. Silence is the worst possible refusal.
	//
	// So a visitor is offered the payable rail and an operator is offered all
	// of them. The role is the test because it is the one this deployment
	// already draws: `Sales User` is what the till requires and what the guest
	// account holds; `System Manager` is the operator.
	//
	// THE PROPER HOME FOR THIS IS A SETTING, not a constant in a page script.
	// `CryptoPoS Settings` has no field for it and adding one is a schema
	// change; this is the honest short version and it is marked as such.
	is_operator() {
		// NOT `frappe.user.has_role()`. Measured in this deployment's desk on
		// 2026-09-02: it EXISTS as a function and returns `undefined` rather
		// than a boolean, so a truthiness test on it is false for everybody --
		// including the operator, whose terminal would have silently lost six
		// rails. `frappe.user_roles` is the array the desk boots with, and the
		// guest account reads ["Accounts User","Sales User","All","Guest",
		// "Desk User"] from it while the operator's carries System Manager.
		const roles = (window.frappe && frappe.user_roles) || [];
		return roles.includes("System Manager");
	}

	offerable(rails) {
		if (this.is_operator()) return rails;
		const offered = rails.filter((rail) => PUBLIC_RAILS.includes(rail.name));
		// NEVER LEAVE A TILL WITH NOTHING. If the publicly-offered rail is
		// disabled, a filtered-to-empty list would render a terminal with no
		// rail and no way to say why. Showing everything is the lesser fault.
		return offered.length ? offered : rails;
	}

	preferred_rail() {
		const preferred = this.rails.find((rail) => PUBLIC_RAILS.includes(rail.name));
		return (preferred || this.rails[0]).name;
	}

	charge() {
		const cents = this.cents();
		if (!cents) return;
		if (!this.rail) {
			// Held AND painted. Every other refusal in this method renders
			// before returning; this one did not, so pressing Charge on a
			// terminal with no rails did nothing whatsoever on screen -- a
			// refusal hidden by omission rather than by a closed panel.
			this.note_error("no rail selected");
			this.render();
			return;
		}
		this.busy = true;
		this.render();
		frappe
			.call({
				method: "cryptopos.api.charge",
				args: { usd_cents: cents, rail_key: this.rail },
			})
			.then((r) => {
				this.busy = false;
				this.sale = r.message;
				this.amount = "";
				this.render();
				this.start_autopoll();
			})
			.catch((e) => {
				this.busy = false;
				// A charge that is refused is a refusal, not an explanation,
				// so it goes on the terminal whatever the log is doing.
				this.note_error(this.reason_from(e) || "charge refused");
				this.render();
			});
	}

	poll({ silent = false } = {}) {
		if (!this.sale) return;
		const name = this.sale.name;
		frappe
			.call({ method: "cryptopos.api.poll", args: { sale_name: name } })
			.then((r) => {
				this.sale = r.message;
				if (this.is_terminal()) {
					this.stop_autopoll();
					if (!this.loyalty) this.load_loyalty(this.sale.name);
				}
				this.render();
			})
			.catch((e) => {
				if (!silent) this.note_error(this.reason_from(e) || "poll failed");
				this.render();
			});
	}

	load_loyalty(sale_name) {
		// Never on the path of a sale. This runs only once a sale has already
		// ended, so a slow or dead policy layer can delay a disclosure and
		// can never delay taking money.
		frappe
			.call({
				method: "cryptopos.api.loyalty_status",
				args: { sale_name: sale_name, account: "" },
			})
			.then((r) => {
				this.loyalty = r.message;
				this.render();
			})
			.catch(() => {
				this.loyalty = { reachable: false, unreachable_because: __("could not be read") };
				this.render();
			});
	}

	reason_from(e) {
		const messages = e && e._server_messages;
		if (messages) {
			try {
				return JSON.parse(JSON.parse(messages)[0]).message;
			} catch (_ignored) {
				/* fall through to the generic message */
			}
		}
		return e && e.message;
	}

	// ------------------------------------------------------------------
	// The rule that makes hiding the log allowable.
	// ------------------------------------------------------------------
	note_error(text) {
		// The activity log is where this belongs. If nobody has opened it,
		// the terminal itself has to say something -- otherwise hiding an
		// explanation has quietly hidden a refusal.
		if (this.show_log) return;
		this.unseen_error = text;
	}

	// ------------------------------------------------------------------
	// Amount entry
	// ------------------------------------------------------------------
	cents() {
		if (!this.amount) return 0;
		return Math.round(parseFloat(this.amount) * 100);
	}

	key(ch) {
		if (this.sale && !this.is_terminal()) return;
		if (ch === "back") {
			this.amount = this.amount.slice(0, -1);
		} else if (ch === ".") {
			if (!this.amount.includes(".")) this.amount += this.amount ? "." : "0.";
		} else {
			const next = this.amount + ch;
			const [, decimals] = next.split(".");
			if (decimals && decimals.length > 2) return;
			if (next.replace(".", "").length > 9) return;
			this.amount = next;
		}
		this.render();
	}

	bind_keyboard() {
		this.keyhandler = (event) => {
			if (!this.$body.is(":visible")) return;
			if ($(event.target).is("input, textarea, select")) return;
			const k = event.key;
			if (/^[0-9]$/.test(k)) this.key(k);
			else if (k === ".") this.key(".");
			else if (k === "Backspace") this.key("back");
			else if (k === "Enter") {
				if (this.sale && this.is_terminal()) this.clear_sale();
				else if (this.sale) this.poll();
				else this.charge();
			} else if (k === "Escape") {
				if (this.sale) this.clear_sale();
				else this.amount = "";
				this.render();
			} else return;
			event.preventDefault();
		};
		$(document).on("keydown.cryptopos", this.keyhandler);
	}

	clear_sale() {
		this.stop_autopoll();
		this.sale = null;
		this.loyalty = null;
		this.show_points = false;
		this.amount = "";
		this.render();
	}

	// ------------------------------------------------------------------
	// Auto-poll
	// ------------------------------------------------------------------
	start_autopoll() {
		if (!this.autopoll || this.timer) return;
		this.timer = setInterval(() => this.poll({ silent: true }), POLL_SECONDS * 1000);
	}

	stop_autopoll() {
		if (this.timer) clearInterval(this.timer);
		this.timer = null;
	}

	is_terminal() {
		return (
			this.sale &&
			["confirmed", "expired", "failed", "needs_review"].includes(this.sale.state)
		);
	}

	// ------------------------------------------------------------------
	// Rendering
	// ------------------------------------------------------------------
	render() {
		const parts = [];
		if (!this.sale) parts.push(this.keypad_html());
		else if (this.is_terminal()) parts.push(this.done_html());
		else parts.push(this.awaiting_html());

		parts.push(this.panels_html());

		this.$body.html(`<div class="cpos">${parts.join("")}</div>`);
		this.wire();
	}

	notice_html() {
		if (!this.unseen_error) return "";
		return `<div class="cpos-notice" role="alert">
			<b>${__("Something was refused.")}</b>
			<span>${frappe.utils.escape_html(this.unseen_error)}</span>
			<button class="cpos-notice-x" data-act="dismiss">&times;</button>
		</div>`;
	}

	keypad_html() {
		const rail = this.rails.find((r) => r.name === this.rail);
		const digits = ["1", "2", "3", "4", "5", "6", "7", "8", "9", ".", "0", "back"];
		const keys = digits
			.map(
				(d) =>
					`<button class="cpos-key${d === "back" ? " cpos-key-back" : ""}" data-key="${d}">${
						d === "back" ? "&#9003;" : d
					}</button>`
			)
			.join("");

		const options = this.rails
			.map(
				(r) =>
					`<option value="${r.name}"${r.name === this.rail ? " selected" : ""}>${
						frappe.utils.escape_html(r.label)
					}</option>`
			)
			.join("");

		// A rail that is "partial" is making a smaller promise than one that
		// "works", and the operator choosing it is entitled to know which
		// before the customer is standing there.
		const maturity =
			rail && rail.maturity !== "works"
				? `<div class="cpos-maturity"><b>${frappe.utils.escape_html(
						rail.maturity
					)}</b> — ${frappe.utils.escape_html(rail.maturity_note || "")}</div>`
				: "";

		return `${this.notice_html()}
		<div class="cpos-card">
			<div class="cpos-amount ${this.amount ? "" : "cpos-amount-empty"}">
				<span class="cpos-cur">$</span>${this.amount || "0"}
			</div>
			<div class="cpos-keys">${keys}</div>
			<select class="cpos-rail" ${this.rails.length ? "" : "disabled"}>${
				options || `<option>${__("no rails configured")}</option>`
			}</select>
			${maturity}
			<button class="cpos-charge" data-act="charge" ${
				this.cents() > 0 && !this.busy ? "" : "disabled"
			}>${this.busy ? __("Charging…") : __("Charge")}</button>
			${rail ? `<div class="cpos-gate">${frappe.utils.escape_html(rail.gate_text || "")}</div>` : ""}
		</div>`;
	}

	// ------------------------------------------------------------------
	// "COVER THIS CHARGE" -- the house pays, because the visitor has no wallet.
	//
	// Only on a rail the house can actually pay. Offering it on `btc` would be
	// a button that cannot work, which is worse than no button: the sale would
	// sit there looking like something was happening.
	//
	// IT PROMISES NOTHING, and the wording is careful about that. The signing
	// key is on the host; this records intent and the host decides, so the
	// honest verb is "asked". What comes back -- covered, or refused with a
	// reason -- arrives on the next poll through `demo_cover_state`.
	cover_html(s) {
		if (!COVERABLE_RAILS.includes(s.rail_key || this.rail)) return "";
		const state = s.demo_cover_state || "";
		const note = s.demo_cover_note || "";
		if (state === "requested" || state === "paying") {
			return `<div class="cpos-cover cpos-cover-wait">${__(
				"Asked the house to cover this. Paying from the demo wallet…"
			)}</div>`;
		}
		if (state === "refused") {
			// THE REASON, VERBATIM. A refusal with no reason is the silent
			// expiry this button exists to replace.
			return `<div class="cpos-cover cpos-cover-no">${__("The house did not cover this")}${
				note ? `: ${frappe.utils.escape_html(note)}` : "."
			}</div>`;
		}
		if (state === "covered") {
			return `<div class="cpos-cover cpos-cover-yes">${__(
				"The house paid this. Waiting for the chain."
			)}</div>`;
		}
		return `<div class="cpos-row"><button class="cpos-primary cpos-cover-btn" data-act="cover">${__(
			"Cover this charge"
		)}</button></div>
		<div class="cpos-cover-hint">${__(
			"No wallet? The demo wallet pays it for you."
		)}</div>`;
	}

	cover() {
		if (!this.sale) return;
		this.busy = true;
		this.render();
		frappe
			.call({ method: "cryptopos.api.request_cover", args: { sale_name: this.sale.name } })
			.then((r) => {
				this.busy = false;
				if (r.message && r.message.demo_cover_state) {
					this.sale.demo_cover_state = r.message.demo_cover_state;
				}
				this.render();
				// Watch for the answer without making the visitor press Poll.
				this.autopoll = true;
				this.start_autopoll();
			})
			.catch((e) => {
				this.busy = false;
				this.note_error(`could not ask the house to cover this: ${e.message || e}`);
				this.render();
			});
	}

	awaiting_html() {
		const s = this.sale;
		const words = { awaiting: __("Awaiting payment"), detected: __("Seen, not yet mined"), confirming: __("Confirming") };

		// What has actually ARRIVED, whenever that is not the whole invoice.
		//
		// The ending cards have always said this -- "PART PAID", and the
		// received-against-invoiced line for an overpayment. The live card
		// never did, so a cashier watching a sale confirm saw the amount ASKED
		// FOR and nothing about the amount PAID. That matters because
		// `_pending_state` in watch.py derives "Confirming" from the best
		// transfer's confirmed flag alone -- not its amount, not attribution,
		// not timeliness (DECISIONS D17). One satoshi against a 39,685 satoshi
		// invoice therefore renders exactly like the real thing, and the
		// operator hands over the goods.
		//
		// The rail card three methods up states the principle this restores:
		// the operator "is entitled to know ... before the customer is
		// standing there."
		//
		// BigInt for the same reason fmt_native uses it: wei does not fit in a
		// double, and a comparison that silently rounds is worse than none.
		let short = "";
		try {
			const credited = BigInt(s.credited_native || "0");
			const invoiced = BigInt(s.invoiced_native || "0");
			if (credited > 0n && credited < invoiced) {
				short = `<div class="cpos-short">${__("Received")} ${this.fmt_native(
					s.credited_native
				)} ${frappe.utils.escape_html(s.unit_name || "")} ${__("so far")} — ${__(
					"short of the invoice"
				)}.</div>`;
			}
		} catch (_ignored) {
			short = "";
		}

		return `${this.notice_html()}
		<div class="cpos-card cpos-await">
			<div class="cpos-state cpos-state-live">${words[s.state] || s.state}</div>
			<div class="cpos-qr">${this.qr_svg(s.qr_modules)}</div>
			<div class="cpos-native">${this.fmt_native(s.invoiced_native)} <span>${
				frappe.utils.escape_html(s.unit_name || "")
			}</span></div>
			${short}
			<div class="cpos-usd">${this.fmt_usd(s.usd_cents)}</div>
			${this.provenance_html(s)}
			<div class="cpos-uri" title="${frappe.utils.escape_html(s.uri || "")}">${
				frappe.utils.escape_html(s.uri || "")
			}</div>
			${this.cover_html(s)}
			<div class="cpos-row">
				<button class="cpos-secondary" data-act="poll">${__("Poll the node")}</button>
				<label class="cpos-check"><input type="checkbox" data-act="autopoll" ${
					this.autopoll ? "checked" : ""
				}> ${__("auto-poll")}</label>
				<button class="cpos-secondary" data-act="cancel">${__("Leave this sale")}</button>
			</div>
			${this.leaving_html()}
			<div class="cpos-gate">${frappe.utils.escape_html(s.gate_text || "")}</div>
			${this.rate_html(s)}
		</div>`;
	}

	// WHAT LEAVING A SALE ACTUALLY DOES, which is less than "Cancel" promised.
	//
	// `clear_sale()` sets `this.sale = null` and makes NO server call. The
	// sale stays `awaiting` and stays payable until its rate lock runs out.
	// The button said "Cancel", which a cashier reads as "this sale is void"
	// -- and a customer holding the QR from thirty seconds ago can still pay
	// it, whereupon it settles and books an invoice for a sale the operator
	// believed was cancelled. Measured on this deployment: CPS-2026-00555 was
	// "cancelled" on screen and expired an hour later with the lock running
	// the whole time.
	//
	// A REAL CANCEL IS NOT AVAILABLE AND SHOULD NOT BE FAKED. The QR is
	// already in the customer's hands; a sale marked terminal early would
	// strand any payment made against it, which is worse than the sale
	// standing. `LEGAL` has no `cancelled` state and D10 says a terminal
	// state never reopens. So the honest fix is the label and this line.
	leaving_html() {
		return `<div class="cpos-leaving">${__(
			"Leaving returns to the keypad. This charge stays payable until its rate lock runs out."
		)}</div>`;
	}

	done_html() {
		const s = this.sale;
		// Four endings, and the copy for each says only what the terminal can
		// stand behind. "Could not verify" is not a softer way of saying
		// unpaid; it is a different claim, and it is the true one.
		const endings = {
			"confirmed:clean": {
				tone: "ok",
				word: __("SETTLED"),
				line: __("Paid in full and confirmed on chain."),
			},
			"confirmed:over": {
				tone: "ok",
				word: __("SETTLED"),
				line: __("Confirmed. More arrived than was invoiced."),
			},
			"expired:clean": {
				tone: "cold",
				word: __("EXPIRED"),
				line: __("The rate lock ran out. Nothing arrived."),
			},
			"expired:under": {
				tone: "warn",
				word: __("PART PAID"),
				line: __("The rate lock ran out with less than the invoiced amount bound."),
			},
			"needs_review:unidentified": {
				tone: "warn",
				word: __("NEEDS REVIEW"),
				line: __("Money was sighted at this address and could not be tied to this sale."),
			},
			"needs_review:unverified": {
				tone: "warn",
				word: __("NEEDS REVIEW"),
				line: __("The last look never reached the chain, so this cannot be called either way."),
			},
			"failed:": { tone: "bad", word: __("FAILED"), line: __("The sale could not proceed.") },
		};
		const ending =
			endings[`${s.state}:${s.end_kind || ""}`] ||
			{ tone: "warn", word: (s.state_word || s.state || "").toUpperCase(), line: "" };

		const over =
			s.end_kind === "over"
				? `<div class="cpos-overpaid">${__("Received")} ${this.fmt_native(
						s.credited_native
					)} ${frappe.utils.escape_html(s.unit_name || "")} ${__("against")} ${this.fmt_native(
						s.invoiced_native
					)} ${__("invoiced")}.</div>`
				: "";

		// WHAT COULD NOT BE BOUND IS THE REMAINDER, NOT THE WHOLE SIGHTING.
		// `sighted_native` is everything the rail saw; `credited_native` is
		// the part of it this sale was paid with, and the library guarantees
		// credited <= sighted (`SettlementDecision.__post_init__` refuses the
		// other way round). So the unbound money is the difference.
		//
		// Testing `sighted > 0` instead made the line fire on every ordinary
		// settled sale, where sighted == credited and the remainder is zero:
		// measured on this instance at 41 of 57 confirmed sales, every one of
		// them with nothing unbound. The visitor read "could not be bound to
		// this sale. It is not booked." directly above "Booked as
		// ACC-SINV-...". A true field behind a false sentence, which is the
		// shape CONTINUE.md §5 keeps a list of.
		const unbound =
			parseInt(s.sighted_native || "0", 10) - parseInt(s.credited_native || "0", 10);
		const sighted =
			unbound > 0
				? `<div class="cpos-sighted">${__("Sighted")} ${this.fmt_native(
						String(unbound)
					)} ${frappe.utils.escape_html(
						s.unit_name || ""
					)} ${__("that could not be bound to this sale. It is not booked.")}</div>`
				: "";

		const reason = s.review_reason
			? `<div class="cpos-reason">${frappe.utils.escape_html(s.review_reason)}</div>`
			: "";

		const booked = s.sales_invoice
			? `<div class="cpos-booked">${__("Booked as")} <a href="/desk/sales-invoice/${
					encodeURIComponent(s.sales_invoice)
				}">${frappe.utils.escape_html(s.sales_invoice)}</a></div>`
			: `<div class="cpos-notbooked">${__("Not booked")} — ${frappe.utils.escape_html(
					s.not_bookable_because || ""
				)}</div>`;

		return `${this.notice_html()}
		<div class="cpos-card cpos-done cpos-tone-${ending.tone}">
			<div class="cpos-state">${ending.word}</div>
			<div class="cpos-endline">${ending.line}</div>
			<div class="cpos-usd">${this.fmt_usd(s.usd_cents)}</div>
			${over}${sighted}${reason}${booked}${this.loyalty_html()}
			<div class="cpos-ref">${frappe.utils.escape_html(s.invoice_id || "")} &middot; ${
				frappe.utils.escape_html(s.invoice_ref || "")
			}</div>
			<button class="cpos-charge" data-act="cancel">${__("New sale")}</button>
		</div>`;
	}

	loyalty_html() {
		// The award is a disclosure, never a claim. Until the network has
		// committed a mint this says WOULD, and the words come from the
		// server so the screen and the receipt cannot drift apart.
		const l = this.loyalty;
		if (!l) return "";

		if (!l.reachable || !l.facts) {
			const why = l.unreachable_because || l.unreadable_because || "";
			return `<div class="cpos-points">
				<div class="cpos-points-head">${__("Points")}</div>
				<div>${__("The policy layer could not be read, so nothing is claimed about points for this sale.")}
				${why ? `<span class="cpos-dim">${frappe.utils.escape_html(why)}</span>` : ""}</div>
			</div>`;
		}

		const award = l.award;
		const line = award
			? frappe.utils.escape_html(award.wording)
			: __("No award record exists for this sale.");

		// EARNING ONLY goes on every surface that mentions points. It is the
		// single claim the operator is most likely to get wrong.
		const notice = `<div class="cpos-earnonly">${frappe.utils.escape_html(l.earning_only)}</div>`;

		const ceilings = this.show_points
			? `<div class="cpos-ceilings">${l.ceilings
					.map(
						([head, body]) =>
							`<div class="cpos-ceiling"><b>${frappe.utils.escape_html(
								head
							)}</b><span>${frappe.utils.escape_html(body)}</span></div>`
					)
					.join("")}
				<div class="cpos-checkit"><b>${__("Check it yourself")}</b>${l.check_it_yourself
					.map(
						([label, url]) =>
							`<div><span>${frappe.utils.escape_html(label)}</span>
							<a href="${frappe.utils.escape_html(url)}" target="_blank"
							rel="noopener noreferrer">${frappe.utils.escape_html(url)}</a></div>`
					)
					.join("")}</div></div>`
			: "";

		return `<div class="cpos-points">
			<div class="cpos-points-head">${__("Points")}
				<button class="cpos-linkish" data-act="points">${
					this.show_points ? __("hide the limits") : __("what are the limits?")
				}</button>
			</div>
			<div class="cpos-award ${award && award.claims_points ? "cpos-award-held" : ""}">${line}</div>
			${award && award.reason ? `<div class="cpos-dim">${frappe.utils.escape_html(award.reason)}</div>` : ""}
			${notice}
			${ceilings}
		</div>`;
	}

	provenance_html(s) {
		// The booking equation is mode AND provenance AND state AND identity.
		// Anything that is not plainly "real money on a real network to an
		// address the operator configured" says so here, on the surface the
		// customer is looking at.
		const flags = [];
		if (s.mode !== "mainnet") flags.push(s.mode);
		if (s.provenance === "SIMULATED") flags.push(__("simulated"));
		if (!s.provenance) flags.push(__("nothing has answered yet"));
		if (s.identity_source !== "config") flags.push(__("address not merchant-configured"));
		if (s.binding === "shared") flags.push(__("shared address"));
		if (!flags.length) return "";
		return `<div class="cpos-flags">${flags
			.map((f) => `<span>${frappe.utils.escape_html(f)}</span>`)
			.join("")}</div>`;
	}

	rate_html(s) {
		if (!s.rate_source) return "";
		return `<div class="cpos-rate">${__("rate")} ${frappe.utils.escape_html(
			s.rate_source
		)} &middot; ${__("locked until")} ${frappe.utils.escape_html(
			(s.rate_lock_end || "").slice(11, 16)
		)}</div>`;
	}

	panels_html() {
		const toggles = `<div class="cpos-toggles">
			<label class="cpos-check"><input type="checkbox" data-act="bench" ${
				this.show_bench ? "checked" : ""
			}> ${__("dev bench")}</label>
			<label class="cpos-check"><input type="checkbox" data-act="log" ${
				this.show_log ? "checked" : ""
			}> ${__("activity log")}</label>
		</div>`;

		let bench = "";
		if (this.show_bench && this.sale) {
			const s = this.sale;
			const rows = [
				["state", s.state],
				["end_kind", s.end_kind || "—"],
				["mode", s.mode],
				["provenance", s.provenance || "—"],
				["identity_source", s.identity_source],
				["binding", s.binding || "—"],
				["address", s.identity_address],
				["invoiced_native", s.invoiced_native],
				["credited_native", s.credited_native],
				["sighted_native", s.sighted_native],
				["rate_microcents", s.rate_microcents],
				["rate_source", s.rate_source],
				["rate_at", s.rate_at],
				["tx_id", s.tx_id || "—"],
				["settled_at", s.settled_at || "—"],
				["bookable", String(s.bookable)],
			];
			bench = `<div class="cpos-panel"><table class="cpos-props">${rows
				.map(
					([k, v]) =>
						`<tr><th>${k}</th><td>${frappe.utils.escape_html(String(v ?? ""))}</td></tr>`
				)
				.join("")}</table></div>`;
		}

		let log = "";
		if (this.show_log) {
			const events = (this.sale && this.sale.events) || [];
			log = `<div class="cpos-panel"><table class="cpos-log">${
				events.length
					? events
							.map(
								(e) =>
									`<tr><td class="cpos-log-t">${frappe.utils.escape_html(
										(e.at || "").slice(11, 19)
									)}</td><td class="cpos-log-s">${frappe.utils.escape_html(
										e.source || ""
									)}</td><td>${frappe.utils.escape_html(
										e.from_state
									)} &rarr; <b>${frappe.utils.escape_html(
										e.to_state
									)}</b> ${frappe.utils.escape_html(e.detail || "")}</td></tr>`
							)
							.join("")
					: `<tr><td class="cpos-empty">${__("nothing yet")}</td></tr>`
			}</table></div>`;
		}

		return toggles + bench + log;
	}

	// ------------------------------------------------------------------
	// QR
	// ------------------------------------------------------------------
	qr_svg(modules) {
		// The encoding was done server-side by the vendored generator; this
		// only draws the bits it was handed. Keeping the encoder in one place
		// is the point -- two encoders eventually disagree, and the way you
		// find out is a customer's phone refusing to scan.
		if (!modules || !modules.rows) return "";
		const quiet = modules.quiet || 4;
		const span = modules.size + quiet * 2;
		let path = "";
		modules.rows.forEach((row, y) => {
			for (let x = 0; x < row.length; x++) {
				if (row[x] === "1") path += `M${x + quiet},${y + quiet}h1v1h-1z`;
			}
		});
		return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${span} ${span}"
			shape-rendering="crispEdges" role="img" aria-label="${__("Payment QR code")}">
			<rect width="100%" height="100%" fill="#ffffff"/>
			<path d="${path}" fill="#111111"/></svg>`;
	}

	// ------------------------------------------------------------------
	// Formatting
	// ------------------------------------------------------------------
	fmt_usd(cents) {
		return `$${((cents || 0) / 100).toFixed(2)}`;
	}

	fmt_native(value) {
		// BigInt because satoshis fit in a double and wei does not, and a
		// display that silently loses the last digits of an amount is the
		// kind of defect that only shows up on an expensive sale.
		try {
			return BigInt(value || "0").toLocaleString();
		} catch (_ignored) {
			return String(value || "0");
		}
	}

	// ------------------------------------------------------------------
	// Events
	// ------------------------------------------------------------------
	wire() {
		this.$body.find("[data-key]").on("click", (e) => this.key($(e.currentTarget).data("key")));
		this.$body.find(".cpos-rail").on("change", (e) => {
			this.rail = e.currentTarget.value;
			this.render();
		});
		this.$body.find('[data-act="charge"]').on("click", () => this.charge());
		this.$body.find('[data-act="poll"]').on("click", () => this.poll());
		this.$body.find('[data-act="cover"]').on("click", () => this.cover());
		this.$body.find('[data-act="cancel"]').on("click", () => this.clear_sale());
		this.$body.find('[data-act="points"]').on("click", () => {
			this.show_points = !this.show_points;
			this.render();
		});
		this.$body.find('[data-act="dismiss"]').on("click", () => {
			this.unseen_error = null;
			this.render();
		});
		this.$body.find('[data-act="autopoll"]').on("change", (e) => {
			this.autopoll = e.currentTarget.checked;
			if (this.autopoll) this.start_autopoll();
			else this.stop_autopoll();
		});
		this.$body.find('[data-act="bench"]').on("change", (e) => {
			this.show_bench = e.currentTarget.checked;
			this.render();
		});
		this.$body.find('[data-act="log"]').on("change", (e) => {
			this.show_log = e.currentTarget.checked;
			// Opening the log is how an unseen error stops being unseen.
			if (this.show_log) this.unseen_error = null;
			this.render();
		});
	}

	inject_styles() {
		if (document.getElementById("cpos-styles")) return;
		const style = document.createElement("style");
		style.id = "cpos-styles";
		style.textContent = `
.cpos { max-width: 27rem; margin: 0 auto; padding: 1rem 0 3rem; }
.cpos-card { background: var(--card-bg); border: 1px solid var(--border-color);
	border-radius: var(--border-radius-lg, 10px); padding: 1.25rem; text-align: center; }
.cpos-amount { font-size: 3rem; font-weight: 600; letter-spacing: -0.02em;
	color: var(--text-color); margin-bottom: 1rem; font-variant-numeric: tabular-nums; }
.cpos-amount-empty { color: var(--text-light, var(--text-muted)); }
.cpos-cur { font-size: 1.75rem; vertical-align: super; margin-right: 0.15rem; }
.cpos-keys { display: grid; grid-template-columns: repeat(3, 1fr); gap: 0.5rem; }
.cpos-key { font-size: 1.35rem; padding: 0.85rem 0; border-radius: 8px;
	border: 1px solid var(--border-color); background: var(--control-bg, var(--bg-color));
	color: var(--text-color); cursor: pointer; font-variant-numeric: tabular-nums; }
.cpos-key:hover { background: var(--fg-hover-color, var(--bg-color)); }
.cpos-key:active { transform: translateY(1px); }
.cpos-rail { width: 100%; margin-top: 0.75rem; padding: 0.6rem; border-radius: 8px;
	border: 1px solid var(--border-color); background: var(--control-bg, var(--bg-color));
	color: var(--text-color); }
.cpos-charge { width: 100%; margin-top: 0.75rem; padding: 0.85rem; font-size: 1.05rem;
	font-weight: 600; border: none; border-radius: 8px; background: var(--primary, #2490ef);
	color: #fff; cursor: pointer; }
.cpos-charge:disabled { opacity: 0.4; cursor: not-allowed; }
.cpos-gate { margin-top: 0.7rem; font-size: 0.75rem; color: var(--text-muted); }
.cpos-maturity { margin-top: 0.5rem; font-size: 0.75rem; color: var(--text-muted);
	background: var(--bg-color); border-radius: 6px; padding: 0.4rem 0.5rem; }
.cpos-state { font-size: 1.35rem; font-weight: 700; letter-spacing: 0.04em; }
.cpos-state-live { color: var(--text-muted); font-size: 0.9rem; font-weight: 600;
	text-transform: uppercase; }
.cpos-qr { margin: 1rem auto; width: 15rem; max-width: 100%; }
.cpos-qr svg { width: 100%; height: auto; display: block; border-radius: 6px; }
.cpos-native { font-size: 1.5rem; font-weight: 600; color: var(--text-color);
	font-variant-numeric: tabular-nums; }
.cpos-native span { font-size: 0.8rem; font-weight: 400; color: var(--text-muted); }
.cpos-usd { font-size: 1rem; color: var(--text-muted); margin-top: 0.15rem; }
.cpos-uri { margin-top: 0.6rem; font-size: 0.68rem; font-family: monospace;
	color: var(--text-muted); overflow-wrap: anywhere; }
.cpos-flags { margin-top: 0.6rem; display: flex; flex-wrap: wrap; gap: 0.3rem;
	justify-content: center; }
.cpos-flags span { font-size: 0.68rem; text-transform: uppercase; letter-spacing: 0.03em;
	background: var(--bg-color); border: 1px solid var(--border-color);
	color: var(--text-muted); border-radius: 99px; padding: 0.1rem 0.5rem; }
.cpos-row { display: flex; gap: 0.5rem; align-items: center; justify-content: center;
	margin-top: 1rem; flex-wrap: wrap; }
.cpos-secondary { padding: 0.5rem 0.8rem; border-radius: 8px; cursor: pointer;
	border: 1px solid var(--border-color); background: var(--control-bg, var(--bg-color));
	color: var(--text-color); font-size: 0.85rem; }
/* The cover button and what comes back from it. Sized like the keypad's
   Charge button because it is the same kind of act: the one thing on the
   screen a visitor is meant to press. */
.cpos-cover-btn { width: 100%; padding: 0.8rem 1rem; border-radius: 10px; border: 0;
	cursor: pointer; background: var(--text-color); color: var(--bg-color);
	font-size: 1rem; font-weight: 600; }
.cpos-cover-hint { margin-top: 0.35rem; font-size: 0.72rem; color: var(--text-muted); }
.cpos-cover { margin-top: 0.8rem; font-size: 0.8rem; border-radius: 8px;
	padding: 0.5rem 0.7rem; border: 1px solid var(--border-color); }
.cpos-cover-wait { color: var(--text-muted); }
.cpos-cover-yes { color: var(--green-600, #22683f); }
/* A REFUSAL IS NOT A WARNING. It is the end of this attempt, and it reads in
   the same red the ending cards use, because a visitor who skims it and waits
   is a visitor watching a sale expire. */
.cpos-cover-no { color: var(--red-600, #a4343a); }
.cpos-check { font-size: 0.8rem; color: var(--text-muted); display: inline-flex;
	align-items: center; gap: 0.3rem; margin: 0; cursor: pointer; }
.cpos-leaving { margin-top: 0.5rem; font-size: 0.72rem; color: var(--text-muted); }
.cpos-rate { margin-top: 0.35rem; font-size: 0.7rem; color: var(--text-muted); }
.cpos-endline { margin-top: 0.4rem; color: var(--text-muted); font-size: 0.9rem; }
.cpos-tone-ok .cpos-state { color: var(--green-600, #22683f); }
.cpos-tone-warn .cpos-state { color: var(--orange-600, #b35309); }
.cpos-tone-bad .cpos-state { color: var(--red-600, #b52a2a); }
.cpos-tone-cold .cpos-state { color: var(--text-muted); }
.cpos-overpaid, .cpos-sighted, .cpos-reason { margin-top: 0.7rem; font-size: 0.8rem;
	text-align: left; border-radius: 6px; padding: 0.5rem 0.6rem;
	background: var(--bg-color); color: var(--text-muted); }
.cpos-reason { border-left: 3px solid var(--orange-500, #d97706); }
.cpos-booked { margin-top: 0.8rem; font-size: 0.85rem; }
.cpos-notbooked { margin-top: 0.8rem; font-size: 0.78rem; color: var(--text-muted); }
.cpos-ref { margin-top: 0.6rem; font-size: 0.7rem; font-family: monospace;
	color: var(--text-muted); }
.cpos-toggles { display: flex; gap: 1rem; justify-content: center; margin-top: 1rem; }
.cpos-panel { margin-top: 0.6rem; background: var(--card-bg);
	border: 1px solid var(--border-color); border-radius: 8px; overflow: auto; max-height: 20rem; }
.cpos-props, .cpos-log { width: 100%; font-size: 0.72rem; font-family: monospace;
	border-collapse: collapse; }
.cpos-props th { text-align: left; font-weight: 500; color: var(--text-muted);
	padding: 0.25rem 0.6rem; white-space: nowrap; vertical-align: top; }
.cpos-props td, .cpos-log td { padding: 0.25rem 0.6rem; color: var(--text-color);
	overflow-wrap: anywhere; vertical-align: top; }
.cpos-log tr:nth-child(odd) { background: var(--bg-color); }
.cpos-log-t, .cpos-log-s { color: var(--text-muted); white-space: nowrap; }
.cpos-empty { color: var(--text-muted); padding: 0.6rem; }
.cpos-notice { display: flex; gap: 0.5rem; align-items: baseline; margin-bottom: 0.75rem;
	padding: 0.6rem 0.75rem; border-radius: 8px; font-size: 0.82rem; text-align: left;
	background: var(--red-50, #fff5f5); color: var(--red-700, #a02020);
	border: 1px solid var(--red-300, #f5c6c6); }
.cpos-points { margin-top: 0.9rem; text-align: left; border-top: 1px solid var(--border-color);
	padding-top: 0.7rem; font-size: 0.8rem; }
.cpos-points-head { font-weight: 600; color: var(--text-color); margin-bottom: 0.35rem;
	display: flex; align-items: baseline; gap: 0.5rem; }
.cpos-linkish { margin-left: auto; border: none; background: none; padding: 0; cursor: pointer;
	font-size: 0.72rem; color: var(--primary, #2490ef); text-decoration: underline; }
.cpos-award { color: var(--text-muted); }
.cpos-award-held { color: var(--green-600, #22683f); font-weight: 600; }
.cpos-earnonly { margin-top: 0.5rem; padding: 0.5rem 0.6rem; border-radius: 6px;
	font-size: 0.72rem; line-height: 1.4;
	background: var(--orange-50, #fff8ec); color: var(--orange-700, #94540b);
	border: 1px solid var(--orange-200, #f5dcb3); }
[data-theme="dark"] .cpos-earnonly { background: #3a2c14; color: #f0c78a; border-color: #6b4f22; }
.cpos-ceilings { margin-top: 0.6rem; display: flex; flex-direction: column; gap: 0.45rem; }
.cpos-ceiling { font-size: 0.72rem; line-height: 1.4; }
.cpos-ceiling b { display: block; color: var(--text-color); }
.cpos-ceiling span { color: var(--text-muted); }
.cpos-checkit { margin-top: 0.3rem; font-size: 0.68rem; }
.cpos-checkit b { display: block; color: var(--text-color); margin-bottom: 0.2rem; }
.cpos-checkit div { display: flex; flex-direction: column; margin-bottom: 0.25rem; }
.cpos-checkit span { color: var(--text-muted); }
.cpos-checkit a { font-family: monospace; overflow-wrap: anywhere; }
.cpos-dim { color: var(--text-muted); font-size: 0.72rem; }
.cpos-notice-x { margin-left: auto; border: none; background: none; cursor: pointer;
	font-size: 1.1rem; line-height: 1; color: inherit; }
[data-theme="dark"] .cpos-notice { background: #3b1d1d; color: #ffb4b4; border-color: #6b2b2b; }
`;
		document.head.appendChild(style);
	}
}
