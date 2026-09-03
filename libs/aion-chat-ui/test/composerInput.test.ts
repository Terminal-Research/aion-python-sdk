import { describe, expect, it } from "vitest";

import {
	createComposerInputState,
	deleteComposerTextBackward,
	deleteComposerTextForward,
	getComposerDraftRows,
	insertComposerText,
	moveComposerCursorHorizontally,
	moveComposerCursorToRowBoundary,
	moveComposerCursorVertically,
	replaceComposerDraft
} from "../src/lib/input/composer.js";

describe("composer input", () => {
	it("inserts text at the cursor and replaces external drafts at the end", () => {
		const inserted = insertComposerText(
			createComposerInputState("helo", 3),
			"l"
		);

		expect(inserted).toEqual({
			draft: "hello",
			cursor: 4
		});
		expect(replaceComposerDraft(inserted, (draft) => `${draft}!`)).toEqual({
			draft: "hello!",
			cursor: 6
		});
	});

	it("gives Backspace and Delete their conventional cursor behavior", () => {
		const state = createComposerInputState("hello", 2);

		expect(deleteComposerTextBackward(state)).toEqual({
			draft: "hllo",
			cursor: 1
		});
		expect(deleteComposerTextForward(state)).toEqual({
			draft: "helo",
			cursor: 2
		});
	});

	it("moves and deletes by grapheme instead of splitting emoji", () => {
		const draft = "A👨‍👩‍👧‍👦B";
		const afterEmoji = draft.length - 1;
		const moved = moveComposerCursorHorizontally(
			createComposerInputState(draft, afterEmoji),
			-1
		);

		expect(moved.cursor).toBe(1);
		expect(deleteComposerTextForward(moved)).toEqual({
			draft: "AB",
			cursor: 1
		});
	});

	it("moves vertically across logical lines while preserving the target column", () => {
		const firstLineEnd = createComposerInputState("abcd\nx\nwxyz", 4);
		const secondLine = moveComposerCursorVertically(firstLineEnd, 1, 20);
		const thirdLine = moveComposerCursorVertically(secondLine, 1, 20);

		expect(secondLine).toEqual({
			draft: "abcd\nx\nwxyz",
			cursor: 6,
			preferredColumn: 4
		});
		expect(thirdLine).toEqual({
			draft: "abcd\nx\nwxyz",
			cursor: 11,
			preferredColumn: 4
		});
	});

	it("moves vertically across soft-wrapped rows", () => {
		const state = createComposerInputState("abcdefgh", 2);
		const nextRow = moveComposerCursorVertically(state, 1, 4);

		expect(nextRow.cursor).toBe(6);
		expect(moveComposerCursorVertically(nextRow, 1, 4).cursor).toBe(8);
	});

	it("moves Home and End to visual row boundaries", () => {
		const state = createComposerInputState("abcdefgh", 6);

		expect(moveComposerCursorToRowBoundary(state, "start", 4).cursor).toBe(4);
		expect(moveComposerCursorToRowBoundary(state, "end", 4).cursor).toBe(8);
	});

	it("wraps using terminal cell width and preserves insertion rows", () => {
		expect(getComposerDraftRows("你a", 2).map((row) => row.text)).toEqual([
			"你",
			"a"
		]);
		expect(getComposerDraftRows("1234", 4).map((row) => row.text)).toEqual([
			"1234",
			""
		]);
	});
});
