export { buildMessageParts } from "./parser";
export type { DetectedSpan, PartExtractor } from "./parser";

export {
	applyFileSuggestion,
	clearFileMention,
	getFileMentionMatch,
	getFileSuggestions,
} from "./mentions";
export type { FileMentionMatch, FileSuggestion } from "./mentions";

export {
	createComposerInputState,
	deleteComposerTextBackward,
	deleteComposerTextForward,
	getComposerContentWidth,
	getComposerCursorRowIndex,
	getComposerDraftRows,
	insertComposerText,
	moveComposerCursorHorizontally,
	moveComposerCursorToRowBoundary,
	moveComposerCursorVertically,
	replaceComposerDraft
} from "./composer.js";
export type {
	ComposerDraftRow,
	ComposerDraftUpdate,
	ComposerInputState,
	VerticalCursorDirection
} from "./composer.js";
