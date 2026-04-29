import type { Part, TextPart } from "@a2a-js/sdk";

import { filePathExtractor } from "./extractors/filePathExtractor.js";

/** A substring match found by a {@link PartExtractor} with its position in the original text. */
export interface DetectedSpan {
	start: number;
	end: number;
	raw: string;
}

/**
 * Plugin interface for detecting and converting specific entities in CLI input text into A2A Parts.
 *
 * `detect` runs synchronously to locate spans; `parse` is async to allow I/O (e.g. reading a file).
 * Return `null` from `parse` to silently skip a span (e.g. file too large, binary content).
 */
export interface PartExtractor<T extends Part = Part> {
	detect(text: string): DetectedSpan[];
	parse(span: DetectedSpan): Promise<T | null>;
}

/** Ordered list of extractors applied to every outgoing message. */
const EXTRACTORS: PartExtractor[] = [filePathExtractor];

function makeTextPart(text: string): TextPart {
	return { kind: "text", text };
}

/**
 * Converts raw CLI input text into an A2A `Part[]` by running all registered extractors.
 *
 * Spans from different extractors are sorted by position; overlapping spans are resolved
 * by keeping the first (highest-priority) match. Text between spans becomes `TextPart`s.
 * Falls back to a single `TextPart` when no extractors produce a match.
 */
export async function buildMessageParts(text: string): Promise<Part[]> {
	const extractors = EXTRACTORS;
	const allSpans: Array<{ span: DetectedSpan; extractor: PartExtractor }> = [];

	for (const extractor of extractors) {
		for (const span of extractor.detect(text)) {
			allSpans.push({ span, extractor });
		}
	}

	allSpans.sort((a, b) => a.span.start - b.span.start);

	const resolved: Array<{ span: DetectedSpan; extractor: PartExtractor }> = [];
	let cursor = 0;
	for (const item of allSpans) {
		if (item.span.start >= cursor) {
			resolved.push(item);
			cursor = item.span.end;
		}
	}

	const parts: Part[] = [];
	let pos = 0;

	for (const { span, extractor } of resolved) {
		const before = text.slice(pos, span.start).trim();
		if (before) {
			parts.push(makeTextPart(before));
		}

		const part = await extractor.parse(span);
		if (part) {
			parts.push(part);
		}

		pos = span.end;
	}

	const remainder = text.slice(pos).trim();
	if (remainder) {
		parts.push(makeTextPart(remainder));
	}

	if (parts.length === 0) {
		parts.push(makeTextPart(text));
	}

	return parts;
}
