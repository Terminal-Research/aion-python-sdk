import React from "react";
import { Box, Text } from "ink";
import { highlight } from "cli-highlight";

import { MARKDOWN_THEME } from "./theme.js";

interface Block {
	kind: "text" | "code";
	content: string;
	language?: string;
}

function parseBlocks(markdown: string): Block[] {
	const blocks: Block[] = [];
	const matcher = /```([a-zA-Z0-9_-]+)?\n([\s\S]*?)```/g;
	let lastIndex = 0;

	for (const match of markdown.matchAll(matcher)) {
		const start = match.index ?? 0;
		if (start > lastIndex) {
			blocks.push({
				kind: "text",
				content: markdown.slice(lastIndex, start)
			});
		}

		blocks.push({
			kind: "code",
			content: match[2]?.trimEnd() ?? "",
			language: match[1]
		});
		lastIndex = start + match[0].length;
	}

	if (lastIndex < markdown.length) {
		blocks.push({
			kind: "text",
			content: markdown.slice(lastIndex)
		});
	}

	return blocks.length > 0 ? blocks : [{ kind: "text", content: markdown }];
}

function renderCode(content: string, language?: string): string {
	if (!content.trim()) {
		return "";
	}

	if (language) {
		try {
			return highlight(content, { language, ignoreIllegals: true });
		} catch {
			// Fall back to plain text rendering below.
		}
	}

	return content;
}

export function MarkdownBlock({ content }: { content: string }): React.JSX.Element {
	const blocks = parseBlocks(content);

	return (
		<Box flexDirection="column">
			{blocks.map((block, index) => {
				if (block.kind === "code") {
					return (
						<Box
							key={`code-${index}`}
							borderStyle="round"
							borderColor={MARKDOWN_THEME.codeBorder}
							paddingX={1}
							flexDirection="column"
							marginBottom={index < blocks.length - 1 ? 1 : 0}
						>
							<Text color={MARKDOWN_THEME.codeText}>{renderCode(block.content, block.language)}</Text>
						</Box>
					);
				}

				return (
					<Text key={`text-${index}`}>
						{block.content.trimEnd() || " "}
					</Text>
				);
			})}
		</Box>
	);
}
