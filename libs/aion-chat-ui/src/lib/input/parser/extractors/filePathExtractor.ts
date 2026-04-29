import { existsSync, readFileSync, statSync } from "node:fs";
import { basename, extname, resolve } from "node:path";

import type { FilePart } from "@a2a-js/sdk";

import type { DetectedSpan, PartExtractor } from "../types";

/** Files larger than this are skipped to avoid bloating the A2A message payload. */
const MAX_FILE_SIZE = 512 * 1024;  // ~0.5 MB

/**
 * Matches absolute paths (`/foo/bar`) and relative paths (`./foo`, `../foo`)
 * preceded by whitespace or at the start of the string.
 * `existsSync` is the real filter — the regex is intentionally loose.
 */
const PATH_PATTERN = /(?:^|\s)(\.{0,2}\/[^\s"'`)\]]+)/gm;

/**
 * Explicit MIME map for extensions common in agent-testing workflows.
 * We avoid the `mime` package because `.ts` maps to `video/mp2t` there,
 * which is wrong for TypeScript source files.
 */
const MIME_TYPES: Record<string, string> = {
	".txt": "text/plain",
	".log": "text/plain",
	".md": "text/markdown",
	".json": "application/json",
	".yaml": "text/yaml",
	".yml": "text/yaml",
	".toml": "text/toml",
	".csv": "text/csv",
	".ts": "text/x-typescript",
	".js": "text/javascript",
	".py": "text/x-python",
	".sh": "text/x-sh",
	".html": "text/html",
	".xml": "text/xml",
	".css": "text/css",
	".env": "text/plain",
	".conf": "text/plain",
	".ini": "text/plain",
};

/** Heuristic binary check: presence of a null byte in the first 512 bytes. */
function isBinary(buffer: Buffer): boolean {
	const limit = Math.min(buffer.length, 512);
	for (let i = 0; i < limit; i++) {
		if (buffer[i] === 0) return true;
	}
	return false;
}

function getMimeType(filePath: string): string {
	return MIME_TYPES[extname(filePath).toLowerCase()] ?? "application/octet-stream";
}

/**
 * Detects file paths in user input and converts them to A2A `FilePart`s with base64 content.
 *
 * Silently skips paths that don't exist, are directories, exceed {@link MAX_FILE_SIZE},
 * or appear to be binary files.
 */
export const filePathExtractor: PartExtractor<FilePart> = {
	detect(text: string): DetectedSpan[] {
		const spans: DetectedSpan[] = [];
		const regex = new RegExp(PATH_PATTERN.source, PATH_PATTERN.flags);
		let match: RegExpExecArray | null;

		while ((match = regex.exec(text)) !== null) {
			const raw = match[1].replace(/[,;:.!?]+$/, "");
			if (!raw) continue;

			const absolute = resolve(raw);
			if (!existsSync(absolute)) continue;

			try {
				if (!statSync(absolute).isFile()) continue;
			} catch {
				continue;
			}

			const start = match.index + match[0].indexOf(raw);
			spans.push({ start, end: start + raw.length, raw: absolute });
		}

		return spans;
	},

	async parse(span: DetectedSpan): Promise<FilePart | null> {
		try {
			const stat = statSync(span.raw);  // span.raw is already absolute after detect()
			if (stat.size > MAX_FILE_SIZE) return null;

			const buffer = readFileSync(span.raw);
			if (isBinary(buffer)) return null;

			return {
				kind: "file",
				file: {
					name: basename(span.raw),
					mimeType: getMimeType(span.raw),
					bytes: buffer.toString("base64"),
				},
			};
		} catch {
			return null;
		}
	},
};
