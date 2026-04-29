import { EXTRACTORS } from "./extractors/index";
import { buildMessageParts as _buildMessageParts } from "./pipeline";

export type { DetectedSpan, PartExtractor } from "./types";

export const buildMessageParts = (text: string) => _buildMessageParts(text, EXTRACTORS);
