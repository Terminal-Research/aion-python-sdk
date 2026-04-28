import { describe, expect, it } from "vitest";

import { buildBrowserOpenCommand } from "../src/lib/browser.js";

describe("buildBrowserOpenCommand", () => {
	it("uses the macOS open command", () => {
		expect(buildBrowserOpenCommand("https://api.aion.to/login", "darwin")).toMatchObject({
			command: "open",
			args: ["https://api.aion.to/login"]
		});
	});

	it("uses xdg-open on Linux", () => {
		expect(buildBrowserOpenCommand("https://api.aion.to/login", "linux")).toMatchObject({
			command: "xdg-open",
			args: ["https://api.aion.to/login"]
		});
	});

	it("uses start through cmd on Windows", () => {
		expect(buildBrowserOpenCommand("https://api.aion.to/login", "win32")).toMatchObject({
			command: "cmd",
			args: ["/c", "start", "", "https://api.aion.to/login"]
		});
	});

	it("rejects non-browser login URLs", () => {
		expect(() => buildBrowserOpenCommand("file:///tmp/login", "darwin")).toThrow(
			"Only HTTP and HTTPS login URLs can be opened"
		);
	});
});
