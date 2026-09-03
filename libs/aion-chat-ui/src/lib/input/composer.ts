import stringWidth from "string-width";

export interface ComposerInputState {
	draft: string;
	cursor: number;
	preferredColumn?: number;
}

export interface ComposerDraftRow {
	text: string;
	start: number;
	end: number;
	width: number;
}

export type ComposerDraftUpdate = string | ((draft: string) => string);
export type VerticalCursorDirection = -1 | 1;

const GRAPHEME_SEGMENTER = new Intl.Segmenter(undefined, {
	granularity: "grapheme"
});

interface GraphemeSegment {
	segment: string;
	index: number;
}

function getGraphemeSegments(value: string): GraphemeSegment[] {
	return Array.from(
		GRAPHEME_SEGMENTER.segment(value),
		({ segment, index }) => ({ segment, index })
	);
}

function getGraphemeBoundaries(value: string): number[] {
	return [
		0,
		...getGraphemeSegments(value).map(
			({ segment, index }) => index + segment.length
		)
	];
}

function normalizeCursor(draft: string, cursor: number): number {
	const clampedCursor = Math.max(0, Math.min(draft.length, cursor));
	let normalizedCursor = 0;

	for (const boundary of getGraphemeBoundaries(draft)) {
		if (boundary > clampedCursor) {
			break;
		}
		normalizedCursor = boundary;
	}

	return normalizedCursor;
}

function resetPreferredColumn(
	state: ComposerInputState,
	cursor: number
): ComposerInputState {
	if (state.cursor === cursor && state.preferredColumn === undefined) {
		return state;
	}

	return {
		draft: state.draft,
		cursor
	};
}

function getCursorAtColumn(row: ComposerDraftRow, column: number): number {
	let cursor = row.start;
	let currentColumn = 0;

	for (const { segment, index } of getGraphemeSegments(row.text)) {
		const nextColumn = currentColumn + stringWidth(segment);
		if (nextColumn > column) {
			break;
		}

		currentColumn = nextColumn;
		cursor = row.start + index + segment.length;
	}

	return cursor;
}

export function getComposerContentWidth(viewportWidth: number): number {
	return Math.max(1, Math.max(24, viewportWidth) - 2);
}

export function getComposerDraftRows(
	draft: string,
	width: number
): ComposerDraftRow[] {
	const safeWidth = Math.max(1, width);
	const sourceLines = draft.split("\n");
	const rows: ComposerDraftRow[] = [];
	let sourceOffset = 0;

	for (const [lineIndex, line] of sourceLines.entries()) {
		const graphemes = getGraphemeSegments(line);

		if (graphemes.length === 0) {
			rows.push({
				text: "",
				start: sourceOffset,
				end: sourceOffset,
				width: 0
			});
		} else {
			let rowStart = 0;
			let rowEnd = 0;
			let rowWidth = 0;

			for (const { segment, index } of graphemes) {
				const graphemeWidth = stringWidth(segment);
				if (rowEnd > rowStart && rowWidth + graphemeWidth > safeWidth) {
					rows.push({
						text: line.slice(rowStart, rowEnd),
						start: sourceOffset + rowStart,
						end: sourceOffset + rowEnd,
						width: rowWidth
					});
					rowStart = index;
					rowWidth = 0;
				}

				rowWidth += graphemeWidth;
				rowEnd = index + segment.length;
			}

			rows.push({
				text: line.slice(rowStart, rowEnd),
				start: sourceOffset + rowStart,
				end: sourceOffset + rowEnd,
				width: rowWidth
			});
		}

		sourceOffset += line.length;
		if (lineIndex < sourceLines.length - 1) {
			sourceOffset += 1;
		}
	}

	const finalRow = rows.at(-1);
	const finalSourceLine = sourceLines.at(-1) ?? "";
	if (
		finalSourceLine.length > 0 &&
		finalRow?.end === draft.length &&
		finalRow.width === safeWidth
	) {
		rows.push({
			text: "",
			start: draft.length,
			end: draft.length,
			width: 0
		});
	}

	return rows;
}

export function getComposerCursorRowIndex(
	rows: readonly ComposerDraftRow[],
	cursor: number
): number {
	let cursorRowIndex = 0;

	for (const [index, row] of rows.entries()) {
		if (cursor < row.start) {
			break;
		}
		if (cursor <= row.end) {
			cursorRowIndex = index;
		}
	}

	return cursorRowIndex;
}

export function createComposerInputState(
	draft = "",
	cursor = draft.length
): ComposerInputState {
	return {
		draft,
		cursor: normalizeCursor(draft, cursor)
	};
}

export function replaceComposerDraft(
	state: ComposerInputState,
	update: ComposerDraftUpdate
): ComposerInputState {
	const draft = typeof update === "function" ? update(state.draft) : update;
	return createComposerInputState(draft);
}

export function insertComposerText(
	state: ComposerInputState,
	text: string
): ComposerInputState {
	if (text.length === 0) {
		return state;
	}

	const cursor = normalizeCursor(state.draft, state.cursor);
	return createComposerInputState(
		`${state.draft.slice(0, cursor)}${text}${state.draft.slice(cursor)}`,
		cursor + text.length
	);
}

export function deleteComposerTextBackward(
	state: ComposerInputState
): ComposerInputState {
	const cursor = normalizeCursor(state.draft, state.cursor);
	const previousCursor = getGraphemeBoundaries(state.draft)
		.filter((boundary) => boundary < cursor)
		.at(-1);
	if (previousCursor === undefined) {
		return resetPreferredColumn(state, cursor);
	}

	return createComposerInputState(
		`${state.draft.slice(0, previousCursor)}${state.draft.slice(cursor)}`,
		previousCursor
	);
}

export function deleteComposerTextForward(
	state: ComposerInputState
): ComposerInputState {
	const cursor = normalizeCursor(state.draft, state.cursor);
	const nextCursor = getGraphemeBoundaries(state.draft).find(
		(boundary) => boundary > cursor
	);
	if (nextCursor === undefined) {
		return resetPreferredColumn(state, cursor);
	}

	return createComposerInputState(
		`${state.draft.slice(0, cursor)}${state.draft.slice(nextCursor)}`,
		cursor
	);
}

export function isComposerBackwardDeleteKey(key: {
	backspace: boolean;
	delete: boolean;
}): boolean {
	// Ink 6 reports the DEL byte emitted by most Backspace keys as `delete`.
	// Its public key object cannot distinguish that byte from forward Delete.
	return key.backspace || key.delete;
}

export function moveComposerCursorHorizontally(
	state: ComposerInputState,
	direction: -1 | 1
): ComposerInputState {
	const cursor = normalizeCursor(state.draft, state.cursor);
	const boundaries = getGraphemeBoundaries(state.draft);
	const cursorBoundaryIndex = boundaries.indexOf(cursor);
	const nextBoundaryIndex = Math.max(
		0,
		Math.min(boundaries.length - 1, cursorBoundaryIndex + direction)
	);

	return resetPreferredColumn(state, boundaries[nextBoundaryIndex] ?? cursor);
}

export function moveComposerCursorToRowBoundary(
	state: ComposerInputState,
	boundary: "start" | "end",
	width: number
): ComposerInputState {
	const cursor = normalizeCursor(state.draft, state.cursor);
	const rows = getComposerDraftRows(state.draft, width);
	const row = rows[getComposerCursorRowIndex(rows, cursor)];
	if (!row) {
		return resetPreferredColumn(state, cursor);
	}

	return resetPreferredColumn(
		state,
		boundary === "start" ? row.start : row.end
	);
}

export function moveComposerCursorVertically(
	state: ComposerInputState,
	direction: VerticalCursorDirection,
	width: number
): ComposerInputState {
	const cursor = normalizeCursor(state.draft, state.cursor);
	const rows = getComposerDraftRows(state.draft, width);
	const currentRowIndex = getComposerCursorRowIndex(rows, cursor);
	const targetRowIndex = Math.max(
		0,
		Math.min(rows.length - 1, currentRowIndex + direction)
	);
	if (targetRowIndex === currentRowIndex) {
		return state;
	}

	const currentRow = rows[currentRowIndex];
	const targetRow = rows[targetRowIndex];
	if (!currentRow || !targetRow) {
		return state;
	}

	const preferredColumn =
		state.preferredColumn ??
		stringWidth(state.draft.slice(currentRow.start, cursor));

	return {
		draft: state.draft,
		cursor: getCursorAtColumn(targetRow, preferredColumn),
		preferredColumn
	};
}
