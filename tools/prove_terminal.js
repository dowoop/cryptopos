/* Fail if any method or control on the terminal page is never reached.
 *
 *   node tools/prove_terminal.js [--quiet]
 *
 * The sibling of `tools/prove.py`, and the same claim in the other language:
 * every function in the page runs at least once, and every control on it is
 * actually clicked, changed or typed at during the suites.
 *
 * The inventory is READ FROM THE SOURCE, never maintained by hand. That is
 * the whole point — a method added tomorrow, or a button added to a template
 * string, joins the required list on its own and fails this gate until
 * something exercises it. A hand-written checklist would go stale the first
 * time somebody was in a hurry.
 *
 * How it works: both suites are re-run as subprocesses with CPOS_COVERAGE
 * pointing at a scratch file. The harness, seeing that variable, wraps the
 * page class and records every method call, every dispatched control and
 * every key pressed. This tool diffs what was recorded against what the
 * source contains.
 */

const fs = require("fs");
const os = require("os");
const path = require("path");
const { spawnSync } = require("child_process");

const ROOT = path.join(__dirname, "..");
const PAGE = path.join(ROOT, "cryptopos", "cryptopos", "page", "terminal", "terminal.js");
const SUITES = ["tests/terminal_render_test.js", "tests/terminal_button_test.js"];

const BOLD = "\x1b[1m";
const DIM = "\x1b[2m";
const GREEN = "\x1b[32m";
const RED = "\x1b[31m";
const RESET = "\x1b[0m";

// ---------------------------------------------------------------------------
// What the page contains.
// ---------------------------------------------------------------------------
function inventory(source) {
	// Methods: one tab of indentation inside the class body.
	const methods = [...source.matchAll(/^\t([a-z_][\w]*)\([^)]*\)\s*\{/gm)].map((m) => m[1]);

	// The desk's entry point is a property assignment, not a class method.
	if (/frappe\.pages\["terminal"\]\.on_page_load\s*=/.test(source)) methods.push("on_page_load");

	// Controls: every literal data-act in the page, plus the keypad's twelve
	// keys (rendered from a loop, so the digits are read from that array) and
	// the rail select, which is found by class rather than by attribute.
	const acts = [...new Set([...source.matchAll(/data-act="([a-z]+)"/g)].map((m) => m[1]))];
	const controls = acts.map((act) => `[data-act="${act}"]`);

	const digitsLine = source.match(/const digits = \[([^\]]+)\]/);
	if (digitsLine) {
		digitsLine[1]
			.split(",")
			.map((part) => part.trim().replace(/^"|"$/g, ""))
			.filter(Boolean)
			.forEach((key) => controls.push(`[data-key="${key}"]`));
	}
	if (source.includes('find(".cpos-rail")')) controls.push(".cpos-rail");

	// Keys the keyboard handler names explicitly.
	const keys = ["Backspace", "Enter", "Escape", "."];
	if (/\/\^\[0-9\]\$\//.test(source)) keys.push("0-9");

	return { methods: [...new Set(methods)], controls: [...new Set(controls)], keys };
}

// ---------------------------------------------------------------------------
// What the suites reached.
// ---------------------------------------------------------------------------
function measure() {
	const file = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "cpos-")), "reached.tsv");
	for (const suite of SUITES) {
		const run = spawnSync(process.execPath, [suite], {
			cwd: ROOT,
			env: Object.assign({}, process.env, { CPOS_COVERAGE: file }),
			encoding: "utf8",
		});
		if (run.status !== 0) {
			console.log(`${RED}${BOLD}${suite} is not green -- fix that before reading coverage${RESET}`);
			console.log(run.stdout.split("\n").filter((line) => line.includes("FAIL")).join("\n"));
			process.exit(1);
		}
	}
	const reached = { method: new Set(), control: new Set(), key: new Set() };
	if (fs.existsSync(file)) {
		fs.readFileSync(file, "utf8")
			.split("\n")
			.filter(Boolean)
			.forEach((line) => {
				const [kind, ...rest] = line.split("\t");
				if (reached[kind]) reached[kind].add(rest.join("\t"));
			});
	}
	return reached;
}

// ---------------------------------------------------------------------------
function main() {
	const quiet = process.argv.includes("--quiet");
	const source = fs.readFileSync(PAGE, "utf8");
	const want = inventory(source);
	const got = measure();

	// A control counts as reached under any event it was dispatched with.
	const controlsReached = new Set([...got.control].map((entry) => entry.split(" ")[0]));

	const missingMethods = want.methods.filter((name) => !got.method.has(name)).sort();
	const missingControls = want.controls.filter((name) => !controlsReached.has(name)).sort();
	const missingKeys = want.keys
		.filter((key) => (key === "0-9" ? ![..."0123456789"].some((d) => got.key.has(d)) : !got.key.has(key)))
		.sort();

	const rows = [
		["methods", want.methods.length, missingMethods],
		["controls", want.controls.length, missingControls],
		["keys", want.keys.length, missingKeys],
	];

	for (const [label, total, missing] of rows) {
		const colour = missing.length ? RED : GREEN;
		const reached = total - missing.length;
		console.log(`  ${colour}${label.padEnd(9)}${RESET}  ${String(reached).padStart(3)}/${total}`);
		if (missing.length && !quiet) {
			missing.forEach((name) => console.log(`      ${DIM}never reached${RESET}  ${name}`));
		}
	}

	const failures = rows.reduce((sum, [, , missing]) => sum + missing.length, 0);
	console.log("");
	if (failures) {
		console.log(`  ${RED}${BOLD}${failures} part(s) of the terminal are never exercised${RESET}`);
		return 1;
	}
	console.log(
		`  ${GREEN}${BOLD}every terminal method runs and every control is operated${RESET} ` +
			`(${want.methods.length} methods, ${want.controls.length} controls)`
	);
	return 0;
}

process.exit(main());
