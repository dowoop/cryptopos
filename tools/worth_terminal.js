/* Break the terminal on purpose. Fail if the suites do not notice.
 *
 *   node tools/worth_terminal.js [--list] [--jobs N]
 *
 * The sibling of `tools/worth.py`, asking the same question of the page that
 * that one asks of the package: not "did this line run?" but "was the
 * assertion around it worth making?"
 *
 * It rewrites one operator, one constant or one negation at a time, points
 * both terminal suites at the rewritten copy, and records whether either of
 * them failed.
 *
 * Mutating JavaScript without a parser dependency means knowing where the
 * CODE is. `terminal.js` is mostly template literals -- 200 lines of it are a
 * CSS block -- and an operator swapped inside a string is not a mutation, it
 * is a typo in some copy. So the source is scanned once into a mask of what
 * is code and what is a string, a comment or a regex, and only code positions
 * are touched. Interpolations inside a template literal count as code again,
 * because they are.
 *
 * Mutants that do not compile are skipped rather than counted as killed. A
 * syntax error proves nothing about a test: every suite "catches" it, and
 * counting those would inflate the score with the one kind of mutant that
 * costs nothing to detect.
 */

const fs = require("fs");
const os = require("os");
const path = require("path");
const vm = require("vm");
const { spawn } = require("child_process");

const ROOT = path.join(__dirname, "..");
const PAGE = path.join(ROOT, "cryptopos", "cryptopos", "page", "terminal", "terminal.js");
const SUITES = ["tests/terminal_button_test.js", "tests/terminal_render_test.js"];

const BOLD = "\x1b[1m";
const DIM = "\x1b[2m";
const GREEN = "\x1b[32m";
const RED = "\x1b[31m";
const RESET = "\x1b[0m";

// ---------------------------------------------------------------------------
// Accepted survivors: mutations that cannot change what anyone can observe.
// Keyed "line:before -> after", each with the reason it is undetectable.
// ---------------------------------------------------------------------------
const EQUIVALENT = {
	// `reachable: false` is read by exactly one condition, `!l.reachable ||
	// !l.facts`, and this object never carries `facts` -- so the second half
	// is already true and the first cannot change the branch taken. The field
	// stays because it documents the shape the server sends.
	"137:false -> true": "the object carries no `facts`, so this branch is taken either way",
	// `parseInt(s.sighted_native || "0", 10)` feeds a `> 0` test, and native
	// amounts are strings of decimal digits. A decimal-digit string is zero in
	// base 11 exactly when it is zero in base 10, so the radix cannot change
	// the predicate. The parsed VALUE is never displayed -- the raw string is.
	"426:10 -> 11": "radix cannot change a `> 0` test on a decimal-digit string",
	// `row[row.length]` is undefined, and `undefined === "1"` is false, so the
	// extra iteration appends no module and draws nothing.
	"620:< -> <=": "one iteration past the end reads undefined and draws nothing",
};

// ---------------------------------------------------------------------------
// Which characters are code.
// ---------------------------------------------------------------------------
function codeMask(source) {
	const mask = new Uint8Array(source.length); // 1 = code
	// A stack so `${ ... }` inside a template literal is code again, and a
	// template literal inside THAT interpolation is a string again.
	const stack = [{ kind: "code" }];
	let i = 0;

	const previousMeaningful = (at) => {
		for (let j = at - 1; j >= 0; j--) {
			if (!/\s/.test(source[j])) return source[j];
		}
		return "";
	};

	while (i < source.length) {
		const top = stack[stack.length - 1];
		const c = source[i];
		const next = source[i + 1];

		if (top.kind === "code") {
			if (c === "/" && next === "/") {
				stack.push({ kind: "line-comment" });
				i += 2;
				continue;
			}
			if (c === "/" && next === "*") {
				stack.push({ kind: "block-comment" });
				i += 2;
				continue;
			}
			if (c === "/" && "(,=:[!&|?{};+".includes(previousMeaningful(i))) {
				stack.push({ kind: "regex" });
				i += 1;
				continue;
			}
			if (c === '"' || c === "'") {
				stack.push({ kind: "string", quote: c });
				i += 1;
				continue;
			}
			if (c === "`") {
				stack.push({ kind: "template" });
				i += 1;
				continue;
			}
			if (c === "}" && stack.length > 1 && top.interpolation) {
				stack.pop();
				i += 1;
				continue;
			}
			if (c === "{" && top.interpolation) top.depth = (top.depth || 0) + 1;
			mask[i] = 1;
			i += 1;
			continue;
		}

		if (top.kind === "line-comment") {
			if (c === "\n") stack.pop();
			i += 1;
			continue;
		}
		if (top.kind === "block-comment") {
			if (c === "*" && next === "/") {
				stack.pop();
				i += 2;
				continue;
			}
			i += 1;
			continue;
		}
		if (top.kind === "string") {
			if (c === "\\") {
				i += 2;
				continue;
			}
			if (c === top.quote) stack.pop();
			i += 1;
			continue;
		}
		if (top.kind === "regex") {
			if (c === "\\") {
				i += 2;
				continue;
			}
			if (c === "/" || c === "\n") stack.pop();
			i += 1;
			continue;
		}
		if (top.kind === "template") {
			if (c === "\\") {
				i += 2;
				continue;
			}
			if (c === "$" && next === "{") {
				stack.push({ kind: "code", interpolation: true, depth: 0 });
				i += 2;
				continue;
			}
			if (c === "`") stack.pop();
			i += 1;
			continue;
		}
		i += 1;
	}
	return mask;
}

// ---------------------------------------------------------------------------
// The mutation operators. Longest tokens first, so `===` is never read as `==`.
// ---------------------------------------------------------------------------
const OPERATORS = [
	["===", "!=="],
	["!==", "==="],
	["<=", "<"],
	[">=", ">"],
	["&&", "||"],
	["||", "&&"],
	["<", "<="],
	[">", ">="],
];

function lineOf(source, index) {
	let line = 1;
	for (let i = 0; i < index; i++) if (source[i] === "\n") line += 1;
	return line;
}

function sites(source) {
	const mask = codeMask(source);
	const found = [];
	const taken = new Uint8Array(source.length);

	for (const [from, to] of OPERATORS) {
		let at = source.indexOf(from);
		while (at !== -1) {
			const clear = [...Array(from.length).keys()].every((k) => mask[at + k] && !taken[at + k]);
			// `<` and `>` also open JSX-ish text and arrow functions; requiring
			// the whole token to be untaken keeps `<=` from being re-read as `<`.
			if (clear && !(from === ">" && source[at - 1] === "=")) {
				for (let k = 0; k < from.length; k++) taken[at + k] = 1;
				found.push({ at, length: from.length, to, description: `${from} -> ${to}` });
			}
			at = source.indexOf(from, at + 1);
		}
	}

	// Numeric literals, and the two boolean ones.
	const literal = /\b(\d+)\b|\b(true|false)\b/g;
	let match;
	while ((match = literal.exec(source))) {
		const at = match.index;
		const text = match[0];
		const clear = [...Array(text.length).keys()].every((k) => mask[at + k] && !taken[at + k]);
		if (!clear) continue;
		// Not part of an identifier or a property name.
		if (/[\w$.]/.test(source[at - 1] || "")) continue;
		const to =
			text === "true" ? "false" : text === "false" ? "true" : String(Number(text) + 1);
		found.push({ at, length: text.length, to, description: `${text} -> ${to}` });
	}

	// Unary `!`, dropped. `!x` becoming `x` is the single most behaviour
	// changing edit available, and the one a missing assertion hides best.
	for (let at = 0; at < source.length; at++) {
		if (source[at] !== "!" || !mask[at] || taken[at]) continue;
		if (source[at + 1] === "=" || source[at - 1] === "=" || source[at - 1] === "!") continue;
		found.push({ at, length: 1, to: " ", description: "drop `!`" });
	}

	return found.sort((a, b) => a.at - b.at);
}

function apply(source, site) {
	return source.slice(0, site.at) + site.to + source.slice(site.at + site.length);
}

function compiles(source) {
	try {
		new vm.Script(source);
		return true;
	} catch (_error) {
		return false;
	}
}

// ---------------------------------------------------------------------------
function runSuite(suite, pagePath) {
	return new Promise((resolve) => {
		const child = spawn(process.execPath, [suite], {
			cwd: ROOT,
			env: Object.assign({}, process.env, { CPOS_PAGE: pagePath }),
			stdio: "ignore",
		});
		const timer = setTimeout(() => {
			child.kill("SIGKILL");
			resolve(1);
		}, 30000);
		child.on("exit", (code) => {
			clearTimeout(timer);
			resolve(code === null ? 1 : code);
		});
	});
}

async function evaluate(source, site, slot) {
	const pagePath = path.join(slot, "terminal.js");
	fs.writeFileSync(pagePath, apply(source, site));
	for (const suite of SUITES) {
		if ((await runSuite(suite, pagePath)) !== 0) return true; // killed
	}
	return false;
}

async function main() {
	const listing = process.argv.includes("--list");
	const jobsFlag = process.argv.indexOf("--jobs");
	const jobs = jobsFlag === -1 ? Math.max(2, os.cpus().length) : Number(process.argv[jobsFlag + 1]);

	const source = fs.readFileSync(PAGE, "utf8");
	const all = sites(source);
	const usable = all.filter((site) => compiles(apply(source, site)));
	const skipped = all.length - usable.length;

	console.log(`  ${DIM}${usable.length} mutants, ${jobs} workers${skipped ? `, ${skipped} skipped (would not compile)` : ""}${RESET}`);

	const root = fs.mkdtempSync(path.join(os.tmpdir(), "cpos-worth-js-"));
	const slots = [];
	for (let i = 0; i < jobs; i++) {
		const slot = path.join(root, String(i));
		fs.mkdirSync(slot);
		slots.push(slot);
	}

	const survivors = [];
	let done = 0;
	let cursor = 0;

	async function worker(slot) {
		while (cursor < usable.length) {
			const site = usable[cursor++];
			const killed = await evaluate(source, site, slot);
			done += 1;
			if (done % 50 === 0) console.log(`  ${DIM}${done}/${usable.length}${RESET}`);
			if (!killed) survivors.push({ line: lineOf(source, site.at), description: site.description });
		}
	}

	await Promise.all(slots.map(worker));
	fs.rmSync(root, { recursive: true, force: true });

	survivors.sort((a, b) => a.line - b.line);
	const accepted = survivors.filter((s) => EQUIVALENT[`${s.line}:${s.description}`]);
	const unexplained = survivors.filter((s) => !EQUIVALENT[`${s.line}:${s.description}`]);

	const killed = usable.length - survivors.length;
	const score = usable.length ? (100 * killed) / usable.length : 100;
	console.log();
	console.log(`  ${unexplained.length ? RED : GREEN}terminal.js${RESET}  ${killed}/${usable.length}  ${score.toFixed(1)}%`);
	console.log();

	if (accepted.length) {
		console.log(`  ${DIM}${accepted.length} accepted as equivalent:${RESET}`);
		accepted.forEach((s) => {
			console.log(`      ${DIM}terminal.js:${s.line}  ${s.description}${RESET}`);
			console.log(`        ${DIM}${EQUIVALENT[`${s.line}:${s.description}`]}${RESET}`);
		});
		console.log();
	}

	if (unexplained.length) {
		console.log(`  ${RED}${BOLD}${unexplained.length} mutant(s) survived${RESET} — the page was wrong and nothing failed`);
		const shown = listing ? unexplained : unexplained.slice(0, 25);
		shown.forEach((s) => console.log(`      terminal.js:${s.line}  ${s.description}`));
		if (!listing && unexplained.length > shown.length) {
			console.log(`      ${DIM}...and ${unexplained.length - shown.length} more; re-run with --list${RESET}`);
		}
		return 1;
	}

	console.log(
		`  ${GREEN}${BOLD}every mutation was caught${RESET} ` +
			`(${killed}/${usable.length} killed, ${accepted.length} accepted as equivalent)`
	);
	return 0;
}

main().then((code) => process.exit(code));
