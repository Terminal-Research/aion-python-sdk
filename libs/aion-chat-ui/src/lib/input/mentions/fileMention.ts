import { readdirSync, statSync } from "node:fs";
import { homedir } from "node:os";
import { basename, dirname, isAbsolute, join, resolve } from "node:path";

export interface FileMentionMatch {
	query: string;
	start: number;
	end: number;
}

export interface FileSuggestion {
	label: string;
	absolutePath: string;
	isDirectory: boolean;
}

const FILE_MENTION_PATTERN = /(?:^|\s)@file:(\S*)$/;

export function getFileMentionMatch(draft: string): FileMentionMatch | undefined {
	const match = FILE_MENTION_PATTERN.exec(draft);
	if (!match) return undefined;

	return {
		query: match[1] ?? "",
		start: match.index + match[0].lastIndexOf("@"),
		end: draft.length
	};
}

export function clearFileMention(draft: string): string {
	const match = getFileMentionMatch(draft);
	if (!match) return draft;
	return draft.slice(0, match.start).trimEnd();
}

/** Lists files and directories matching the query prefix. Directories are shown with trailing `/`. */
export function getFileSuggestions(query: string, limit = 8): FileSuggestion[] {
	try {
		const expanded = query.startsWith("~/") ? `${homedir()}/${query.slice(2)}` : query;

		let dirPart: string;
		let filePart: string;

		if (expanded.endsWith("/")) {
			dirPart = expanded;
			filePart = "";
		} else if (expanded.includes("/")) {
			dirPart = dirname(expanded);
			filePart = basename(expanded);
		} else {
			dirPart = ".";
			filePart = expanded;
		}

		const absDir = isAbsolute(dirPart) ? dirPart : resolve(dirPart);

		return readdirSync(absDir)
			.filter((name) => name.startsWith(filePart) && !name.startsWith("."))
			.flatMap((name) => {
				const abs = join(absDir, name);
				try {
					const isDirectory = statSync(abs).isDirectory();
					return [{ label: isDirectory ? `${name}/` : name, absolutePath: abs, isDirectory }];
				} catch {
					return [];
				}
			})
			.slice(0, limit);
	} catch {
		return [];
	}
}

/**
 * Applies a file suggestion to the draft.
 * Directories insert `@file:<path>/` to keep the menu open for further navigation.
 * Files insert the absolute path and close the mention.
 */
export function applyFileSuggestion(draft: string, suggestion: FileSuggestion): string {
	const match = getFileMentionMatch(draft);
	if (!match) return draft;
	const before = draft.slice(0, match.start).trimEnd();

	if (suggestion.isDirectory) {
		const mention = `@file:${suggestion.absolutePath}/`;
		return before ? `${before} ${mention}` : mention;
	}

	return before ? `${before} ${suggestion.absolutePath}` : suggestion.absolutePath;
}
