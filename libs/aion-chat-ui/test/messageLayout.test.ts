import { describe, expect, it } from "vitest";

import { wrapToWidth } from "../src/components/messages/messageLayout.js";

describe("messageLayout", () => {
	it("wraps prose at word boundaries", () => {
		expect(wrapToWidth("I am online and ready", 12)).toEqual([
			"I am online",
			"and ready"
		]);
	});

	it("preserves explicit blank lines while wrapping each source line", () => {
		expect(wrapToWidth("first line\n\nsecond line wraps softly", 12)).toEqual([
			"first line",
			"",
			"second line",
			"wraps softly"
		]);
	});

	it("hard-wraps tokens that are longer than the available width", () => {
		expect(wrapToWidth("supercalifragilistic", 8)).toEqual([
			"supercal",
			"ifragili",
			"stic"
		]);
	});
});
