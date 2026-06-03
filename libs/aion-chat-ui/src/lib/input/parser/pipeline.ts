import type { Part } from "@a2a-js/sdk";

import { makeTextPart } from "../../a2aProtocol.js";

import type { DetectedSpan, PartExtractor } from "./types";

export async function buildMessageParts(text: string, extractors: PartExtractor[]): Promise<Part[]> {
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
		if (before) parts.push(makeTextPart(before));

		const part = await extractor.parse(span);
		if (part) parts.push(part);

		pos = span.end;
	}

	const remainder = text.slice(pos).trim();
	if (remainder) parts.push(makeTextPart(remainder));

	if (parts.length === 0) parts.push(makeTextPart(text));

	return parts;
}
