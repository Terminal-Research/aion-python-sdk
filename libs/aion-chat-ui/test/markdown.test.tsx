import { stripVTControlCharacters } from "node:util";

import React from "react";
import { renderToString } from "ink";
import { describe, expect, it } from "vitest";

import {
	MarkdownBlock,
	sanitizeMarkdownText,
	splitMarkdownForStreaming
} from "../src/lib/markdown.js";

function renderMarkdown(
	content: string,
	{
		width = 60,
		isFinalized = true
	}: { width?: number; isFinalized?: boolean } = {}
): { output: string; text: string } {
	const output = renderToString(
		<MarkdownBlock
			content={content}
			width={width}
			isFinalized={isFinalized}
		/>,
		{ columns: width }
	);
	return { output, text: stripVTControlCharacters(output) };
}

describe("MarkdownBlock", () => {
	it("renders headings and inline Markdown with Ink text styles", () => {
		const { text } = renderMarkdown(
			"# Heading\n\nParagraph with **bold**, *italic*, ~~removed~~, and `code`."
		);

		expect(text).toContain("Heading");
		expect(text).toContain("Paragraph with bold, italic, removed, and code.");
		expect(text).not.toContain("# Heading");
		expect(text).not.toContain("**bold**");
	});

	it("renders block quotes, lists, and task markers", () => {
		const { text } = renderMarkdown(
			"> Quoted text\n\n- First\n- [x] Complete\n- [ ] Pending"
		);

		expect(text).toContain("│ Quoted text");
		expect(text).toContain("• First");
		expect(text).toContain("[x] Complete");
		expect(text).toContain("[ ] Pending");
	});

	it("renders fenced code without exposing Markdown fences", () => {
		const { text } = renderMarkdown("```ts\nconst answer = 42;\n```");

		expect(text).toContain("╭");
		expect(text).toContain("const answer = 42;");
		expect(text).not.toContain("```ts");
	});

	it("renders tables as a grid when space is available", () => {
		const { text } = renderMarkdown(
			"| Name | Status |\n| --- | --- |\n| Aion | Ready |",
			{ width: 50 }
		);

		expect(text).toContain("Name");
		expect(text).toContain(" │ ");
		expect(text).toContain("Aion");
		expect(text).toContain("Ready");
	});

	it("renders narrow tables as labeled rows", () => {
		const { text } = renderMarkdown(
			"| Name | Status |\n| --- | --- |\n| Aion | Ready |",
			{ width: 16 }
		);

		expect(text).toContain("Name: Aion");
		expect(text).toContain("Status: Ready");
	});

	it("keeps the unfinished streaming line literal until a newline arrives", () => {
		const pending = renderMarkdown("**bold**", { isFinalized: false });
		const completedLine = renderMarkdown("**bold**\nnext", {
			isFinalized: false
		});
		const finalized = renderMarkdown("**bold**", { isFinalized: true });

		expect(pending.text).toContain("**bold**");
		expect(completedLine.text).toContain("bold");
		expect(completedLine.text).not.toContain("**bold**");
		expect(completedLine.text).toContain("next");
		expect(finalized.text).toContain("bold");
		expect(finalized.text).not.toContain("**bold**");
	});

	it("splits active Markdown at the last completed line", () => {
		expect(splitMarkdownForStreaming("first\nsecond", false)).toEqual({
			parsedContent: "first\n",
			pendingContent: "second"
		});
		expect(splitMarkdownForStreaming("first\nsecond", true)).toEqual({
			parsedContent: "first\nsecond",
			pendingContent: ""
		});
	});

	it("removes terminal control sequences from agent text", () => {
		const unsafe = "safe\u001B]8;;https://example.com\u0007click\u001B]8;;\u0007";
		const sanitized = sanitizeMarkdownText(unsafe);

		expect(sanitized).toBe("safeclick");
		expect(sanitized).not.toContain("\u001B");
	});
});
