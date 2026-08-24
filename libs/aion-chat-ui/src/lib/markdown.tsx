import { stripVTControlCharacters } from "node:util";

import React, { memo, useMemo } from "react";
import { Box, Text } from "ink";
import { marked, type Token, type Tokens } from "marked";

import { MARKDOWN_THEME } from "./theme.js";

const UNSAFE_CONTROL_CHARACTERS =
	/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F-\u009F]/gu;

export interface StreamingMarkdownParts {
	parsedContent: string;
	pendingContent: string;
}

export function sanitizeMarkdownText(value: string): string {
	return stripVTControlCharacters(value).replace(UNSAFE_CONTROL_CHARACTERS, "");
}

export function splitMarkdownForStreaming(
	content: string,
	isFinalized: boolean
): StreamingMarkdownParts {
	if (isFinalized) {
		return { parsedContent: content, pendingContent: "" };
	}

	const lastNewlineIndex = content.lastIndexOf("\n");
	if (lastNewlineIndex === -1) {
		return { parsedContent: "", pendingContent: content };
	}

	return {
		parsedContent: content.slice(0, lastNewlineIndex + 1),
		pendingContent: content.slice(lastNewlineIndex + 1)
	};
}

function renderCode(content: string): string {
	const sanitizedContent = sanitizeMarkdownText(content);
	if (!sanitizedContent.trim()) {
		return "";
	}
	return sanitizedContent;
}

function nestedTokens(token: Token): Token[] {
	return "tokens" in token && Array.isArray(token.tokens) ? token.tokens : [];
}

function renderInlineTokens(tokens: Token[], keyPrefix: string): React.ReactNode[] {
	return tokens.map((token, index) => {
		const key = `${keyPrefix}-${index}`;
		switch (token.type) {
			case "text": {
				const children = nestedTokens(token);
				return children.length > 0 ? (
					<React.Fragment key={key}>
						{renderInlineTokens(children, key)}
					</React.Fragment>
				) : (
					<React.Fragment key={key}>
						{sanitizeMarkdownText(token.text)}
					</React.Fragment>
				);
			}
			case "escape":
				return (
					<React.Fragment key={key}>
						{sanitizeMarkdownText(token.text)}
					</React.Fragment>
				);
			case "strong":
				return (
					<Text key={key} bold>
						{renderInlineTokens(nestedTokens(token), key)}
					</Text>
				);
			case "em":
				return (
					<Text key={key} italic>
						{renderInlineTokens(nestedTokens(token), key)}
					</Text>
				);
			case "del":
				return (
					<Text key={key} strikethrough>
						{renderInlineTokens(nestedTokens(token), key)}
					</Text>
				);
			case "codespan":
				return (
					<Text key={key} color={MARKDOWN_THEME.inlineCode}>
						{sanitizeMarkdownText(token.text)}
					</Text>
				);
			case "link":
				return (
					<React.Fragment key={key}>
						<Text color={MARKDOWN_THEME.link} underline>
							{renderInlineTokens(nestedTokens(token), key)}
						</Text>
						<Text color={MARKDOWN_THEME.muted}>
							{` (${sanitizeMarkdownText(token.href)})`}
						</Text>
					</React.Fragment>
				);
			case "image":
				return (
					<Text key={key} color={MARKDOWN_THEME.muted}>
						{`[image: ${sanitizeMarkdownText(token.text)}] (${sanitizeMarkdownText(token.href)})`}
					</Text>
				);
			case "br":
				return <React.Fragment key={key}>{"\n"}</React.Fragment>;
			case "html":
				return (
					<React.Fragment key={key}>
						{sanitizeMarkdownText(token.text)}
					</React.Fragment>
				);
			default: {
				const children = nestedTokens(token);
				return children.length > 0 ? (
					<React.Fragment key={key}>
						{renderInlineTokens(children, key)}
					</React.Fragment>
				) : (
					<React.Fragment key={key}>
						{sanitizeMarkdownText(token.raw)}
					</React.Fragment>
				);
			}
		}
	});
}

function inlineText(tokens: Token[]): string {
	return tokens
		.map((token) => {
			switch (token.type) {
				case "text":
				case "escape":
				case "codespan":
				case "html":
					return sanitizeMarkdownText(token.text);
				case "image":
					return sanitizeMarkdownText(token.text);
				case "br":
					return " ";
				default:
					return inlineText(nestedTokens(token));
			}
		})
		.join("");
}

function renderTableRow({
	cells,
	columnWidths,
	header,
	keyPrefix
}: {
	cells: Tokens.TableCell[];
	columnWidths: number[];
	header: boolean;
	keyPrefix: string;
}): React.JSX.Element {
	return (
		<Box>
			{cells.map((cell, index) => (
				<React.Fragment key={`${keyPrefix}-${index}`}>
					{index > 0 ? (
						<Text color={MARKDOWN_THEME.tableBorder}>{" │ "}</Text>
					) : null}
					<Box
						width={columnWidths[index] ?? 1}
						justifyContent={
							cell.align === "right"
								? "flex-end"
								: cell.align === "center"
									? "center"
									: "flex-start"
						}
					>
						<Text color={header ? MARKDOWN_THEME.tableHeader : undefined} bold={header}>
							{renderInlineTokens(cell.tokens, `${keyPrefix}-${index}`)}
						</Text>
					</Box>
				</React.Fragment>
			))}
		</Box>
	);
}

function renderTable(
	token: Tokens.Table,
	width: number,
	keyPrefix: string
): React.JSX.Element {
	const columnCount = token.header.length;
	const separatorWidth = Math.max(0, columnCount - 1) * 3;
	const useGrid = columnCount > 0 && width >= columnCount * 8 + separatorWidth;
	if (!useGrid) {
		const labels = token.header.map((cell, index) => {
			const label = inlineText(cell.tokens).trim();
			return label || `Column ${index + 1}`;
		});
		return (
			<Box flexDirection="column">
				{token.rows.map((row, rowIndex) => (
					<Box
						key={`${keyPrefix}-row-${rowIndex}`}
						flexDirection="column"
						marginBottom={rowIndex < token.rows.length - 1 ? 1 : 0}
					>
						{row.map((cell, cellIndex) => (
							<Text key={`${keyPrefix}-row-${rowIndex}-${cellIndex}`}>
								<Text bold color={MARKDOWN_THEME.tableHeader}>
									{labels[cellIndex]}
								</Text>
								{": "}
								{renderInlineTokens(
									cell.tokens,
									`${keyPrefix}-row-${rowIndex}-${cellIndex}`
								)}
							</Text>
						))}
					</Box>
				))}
			</Box>
		);
	}

	const availableWidth = width - separatorWidth;
	const baseWidth = Math.floor(availableWidth / columnCount);
	const remainder = availableWidth % columnCount;
	const columnWidths = Array.from(
		{ length: columnCount },
		(_, index) => baseWidth + (index < remainder ? 1 : 0)
	);
	return (
		<Box flexDirection="column">
			{renderTableRow({
				cells: token.header,
				columnWidths,
				header: true,
				keyPrefix: `${keyPrefix}-header`
			})}
			<Text color={MARKDOWN_THEME.tableBorder}>{"─".repeat(width)}</Text>
			{token.rows.map((row, rowIndex) => (
				<React.Fragment key={`${keyPrefix}-row-${rowIndex}`}>
					{renderTableRow({
						cells: row,
						columnWidths,
						header: false,
						keyPrefix: `${keyPrefix}-row-${rowIndex}`
					})}
				</React.Fragment>
			))}
		</Box>
	);
}

function renderBlockToken(
	token: Token,
	width: number,
	keyPrefix: string
): React.ReactNode {
	switch (token.type) {
		case "paragraph":
		case "text": {
			const children = nestedTokens(token);
			return (
				<Text color={MARKDOWN_THEME.foreground}>
					{children.length > 0
						? renderInlineTokens(children, keyPrefix)
						: sanitizeMarkdownText(token.text)}
				</Text>
			);
		}
		case "heading": {
			const heading = token as Tokens.Heading;
			const color =
				heading.depth === 1
					? MARKDOWN_THEME.headingPrimary
					: heading.depth === 2
						? MARKDOWN_THEME.headingSecondary
						: MARKDOWN_THEME.headingTertiary;
			return (
				<Text color={color} bold underline={heading.depth === 1}>
					{renderInlineTokens(heading.tokens, keyPrefix)}
				</Text>
			);
		}
		case "code": {
			const code = token as Tokens.Code;
			const language = sanitizeMarkdownText(
				code.lang?.trim().split(/\s+/u)[0] ?? ""
			);
			return (
				<Box
					borderStyle="round"
					borderColor={MARKDOWN_THEME.codeBorder}
					paddingX={1}
					flexDirection="column"
					width={Math.max(4, width)}
				>
					{language ? (
						<Text color={MARKDOWN_THEME.muted}>{language}</Text>
					) : null}
					<Text color={MARKDOWN_THEME.codeText}>
						{renderCode(code.text)}
					</Text>
				</Box>
			);
		}
		case "blockquote": {
			const blockquote = token as Tokens.Blockquote;
			return (
				<Box>
					<Text color={MARKDOWN_THEME.blockquoteBorder}>{"│ "}</Text>
					<Box flexDirection="column" width={Math.max(1, width - 2)}>
						{renderBlockTokens(
							blockquote.tokens,
							Math.max(1, width - 2),
							`${keyPrefix}-quote`,
							true
						)}
					</Box>
				</Box>
			);
		}
		case "list": {
			const list = token as Tokens.List;
			const start = typeof list.start === "number" ? list.start : 1;
			return (
				<Box flexDirection="column">
					{list.items.map((item, index) => {
						const marker = item.task
							? item.checked
								? "[x] "
								: "[ ] "
							: list.ordered
								? `${start + index}. `
								: "• ";
						return (
							<Box key={`${keyPrefix}-item-${index}`}>
								<Text color={MARKDOWN_THEME.listMarker}>{marker}</Text>
								<Box
									flexDirection="column"
									width={Math.max(1, width - marker.length)}
								>
									{renderBlockTokens(
										item.tokens,
										Math.max(1, width - marker.length),
										`${keyPrefix}-item-${index}`,
										true
									)}
								</Box>
							</Box>
						);
					})}
				</Box>
			);
		}
		case "table":
			return renderTable(token as Tokens.Table, width, keyPrefix);
		case "hr":
			return (
				<Text color={MARKDOWN_THEME.muted}>{"─".repeat(Math.max(1, width))}</Text>
			);
		case "html":
			return <Text>{sanitizeMarkdownText(token.text)}</Text>;
		default: {
			const children = nestedTokens(token);
			return children.length > 0 ? (
				<Box flexDirection="column">
					{renderBlockTokens(children, width, `${keyPrefix}-nested`, true)}
				</Box>
			) : (
				<Text>{sanitizeMarkdownText(token.raw)}</Text>
			);
		}
	}
}

function renderBlockTokens(
	tokens: Token[],
	width: number,
	keyPrefix: string,
	compact = false
): React.ReactNode[] {
	const visibleTokens = tokens.filter(
		(token) => token.type !== "space" && token.type !== "def"
	);
	return visibleTokens.map((token, index) => (
		<Box
			key={`${keyPrefix}-${index}`}
			flexDirection="column"
			marginBottom={!compact && index < visibleTokens.length - 1 ? 1 : 0}
		>
			{renderBlockToken(token, width, `${keyPrefix}-${index}`)}
		</Box>
	));
}

export const MarkdownBlock = memo(function MarkdownBlock({
	content,
	width,
	isFinalized
}: {
	content: string;
	width: number;
	isFinalized: boolean;
}): React.JSX.Element {
	const sanitizedContent = useMemo(() => sanitizeMarkdownText(content), [content]);
	const { parsedContent, pendingContent } = splitMarkdownForStreaming(
		sanitizedContent,
		isFinalized
	);
	const tokens = useMemo<Token[] | undefined>(() => {
		if (!parsedContent) {
			return [];
		}

		try {
			return marked.lexer(parsedContent, { gfm: true });
		} catch {
			return undefined;
		}
	}, [parsedContent]);
	if (tokens === undefined) {
		return (
			<Box width={Math.max(1, width)}>
				<Text color={MARKDOWN_THEME.foreground}>{sanitizedContent}</Text>
			</Box>
		);
	}

	return (
		<Box flexDirection="column" width={Math.max(1, width)}>
			{renderBlockTokens(tokens, Math.max(1, width), "markdown")}
			{pendingContent ? (
				<Text color={MARKDOWN_THEME.foreground}>{pendingContent}</Text>
			) : null}
			{!parsedContent && !pendingContent ? <Text>{" "}</Text> : null}
		</Box>
	);
});
