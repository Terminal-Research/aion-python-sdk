import ansiEscapes from "ansi-escapes";
import { describe, expect, it, vi } from "vitest";

import { requestTerminalClear } from "../src/lib/terminal.js";

describe("requestTerminalClear", () => {
	it("requests a screen and scrollback clear for an interactive terminal", () => {
		const write = vi.fn();

		expect(
			requestTerminalClear({
				isTTY: true,
				terminalType: "xterm-256color",
				write
			})
		).toBe(true);
		expect(write).toHaveBeenCalledOnce();
		expect(write).toHaveBeenCalledWith(ansiEscapes.clearTerminal);
	});

	it.each([
		{ isTTY: false, terminalType: "xterm-256color" },
		{ isTTY: undefined, terminalType: "xterm-256color" },
		{ isTTY: true, terminalType: "dumb" },
		{ isTTY: true, terminalType: "DUMB" }
	])("skips unsupported terminal output: %o", ({ isTTY, terminalType }) => {
		const write = vi.fn();

		expect(requestTerminalClear({ isTTY, terminalType, write })).toBe(false);
		expect(write).not.toHaveBeenCalled();
	});

	it("keeps logical clearing usable if the terminal write fails", () => {
		expect(
			requestTerminalClear({
				isTTY: true,
				terminalType: "xterm-256color",
				write: () => {
					throw new Error("terminal unavailable");
				}
			})
		).toBe(false);
	});
});
