import React, {
	type RefObject,
	useEffect,
	useLayoutEffect,
	useRef,
	useState
} from "react";
import {
	Box,
	Text,
	type CursorPosition,
	type DOMElement,
	useCursor,
	useStdout
} from "ink";

import { COMPOSER_THEME } from "../lib/theme.js";

const INPUT_BACKGROUND = COMPOSER_THEME.background;
const INPUT_FOREGROUND = COMPOSER_THEME.foreground;
const INPUT_PLACEHOLDER = COMPOSER_THEME.placeholder;
const INPUT_PRIMARY = COMPOSER_THEME.primary;
const INPUT_ACCENT = COMPOSER_THEME.accent;
const SECONDARY_TEXT = COMPOSER_THEME.muted;
const PRIMARY_TEXT = INPUT_FOREGROUND;
const SELECTION_HIGHLIGHT = COMPOSER_THEME.selection;
const MENU_INDENT = "  ";

export interface ComposerMenuItem {
	label: string;
	description: string;
}

export interface AgentSuggestionView {
	agentKey: string;
	id: string;
	sourceName: string;
	description: string;
}

export interface SlashSubmenuView {
	title: string;
	subtitle: string;
	options: readonly ComposerMenuItem[];
	selectedIndex: number;
}

export interface ChatComposerProps {
	draft: string;
	activeAgentId?: string;
	discoveredCount: number;
	pushState: string;
	streamState: string;
	agentSuggestions: readonly AgentSuggestionView[];
	selectedSuggestionIndex: number;
	fileSuggestions: string[];
	selectedFileSuggestionIndex: number;
	slashCommands: readonly ComposerMenuItem[];
	selectedSlashCommandIndex: number;
	slashMenuVisible: boolean;
	slashSubmenu?: SlashSubmenuView;
}

function buildControls(hasDraft: boolean): string[] {
	const controls = ["Enter sends", "Shift+Enter newline"];
	controls.push(hasDraft ? "Ctrl+C clears" : "Ctrl+C exits");
	return controls;
}

function wrapToWidth(value: string, width: number): string[] {
	const safeWidth = Math.max(1, width);
	const sourceLines = value.split("\n");
	const rows: string[] = [];

	for (const line of sourceLines) {
		if (line.length === 0) {
			rows.push("");
			continue;
		}

		for (let index = 0; index < line.length; index += safeWidth) {
			rows.push(line.slice(index, index + safeWidth));
		}
	}

	return rows;
}

export function wrapComposerDraft(value: string, width: number): string[] {
	const safeWidth = Math.max(1, width);
	const rows = wrapToWidth(value, safeWidth);
	const finalSourceLine = value.split("\n").at(-1) ?? "";

	if (
		finalSourceLine.length > 0 &&
		finalSourceLine.length % safeWidth === 0
	) {
		// Keep the insertion point inside the composer after a full row.
		rows.push("");
	}

	return rows;
}

function measureCursorPosition(
	anchor: DOMElement | null
): CursorPosition | undefined {
	if (!anchor?.yogaNode) {
		return undefined;
	}

	let current: DOMElement | undefined = anchor;
	let x = 0;
	let y = 0;

	while (current?.parentNode) {
		if (!current.yogaNode) {
			return undefined;
		}

		x += current.yogaNode.getComputedLeft();
		y += current.yogaNode.getComputedTop();
		current = current.parentNode;
	}

	return {
		x: x + anchor.yogaNode.getComputedWidth(),
		y
	};
}

function cursorPositionsMatch(
	current: CursorPosition | undefined,
	next: CursorPosition | undefined
): boolean {
	return current?.x === next?.x && current?.y === next?.y;
}

function useComposerCursor(
	anchorRef: RefObject<DOMElement | null>,
	enabled: boolean
): void {
	const { setCursorPosition } = useCursor();
	const [measuredPosition, setMeasuredPosition] =
		useState<CursorPosition>();

	setCursorPosition(enabled ? measuredPosition : undefined);

	useLayoutEffect(() => {
		// Yoga coordinates are current only after Ink commits the latest layout.
		const nextPosition = enabled
			? measureCursorPosition(anchorRef.current)
			: undefined;

		setMeasuredPosition((currentPosition) =>
			cursorPositionsMatch(currentPosition, nextPosition)
				? currentPosition
				: nextPosition
		);
	});
}

function padLabel(label: string, width: number): string {
	return `${label}${" ".repeat(Math.max(0, width - label.length))}`;
}

function truncateText(value: string, width: number): string {
	const inlineValue = value.replace(/\s+/gu, " ").trim();
	if (width <= 0) {
		return "";
	}
	if (inlineValue.length <= width) {
		return inlineValue;
	}
	if (width <= 3) {
		return inlineValue.slice(0, width);
	}
	return `${inlineValue.slice(0, width - 3)}...`;
}

function formatColumn(value: string, width: number): string {
	return padLabel(truncateText(value, width), width);
}

function getTableLabelWidth(
	items: readonly ComposerMenuItem[],
	withNumbers = false
): number {
	return items.reduce((maxWidth, item, index) => {
		const label = withNumbers ? `${index + 1}. ${item.label}` : item.label;
		return Math.max(maxWidth, label.length);
	}, 0);
}

function getAgentSuggestionLabelWidth(
	items: readonly AgentSuggestionView[]
): number {
	return items.reduce(
		(maxWidth, item) => Math.max(maxWidth, `@${item.id}`.length),
		0
	);
}

function getAgentSuggestionSourceWidth(
	items: readonly AgentSuggestionView[]
): number {
	return items.reduce(
		(maxWidth, item) => Math.max(maxWidth, item.sourceName.length),
		0
	);
}

export function ChatComposer({
	draft,
	activeAgentId,
	discoveredCount,
	pushState,
	streamState,
	agentSuggestions,
	selectedSuggestionIndex,
	fileSuggestions,
	selectedFileSuggestionIndex,
	slashCommands,
	selectedSlashCommandIndex,
	slashMenuVisible,
	slashSubmenu
}: ChatComposerProps): React.JSX.Element {
	const { stdout } = useStdout();
	const cursorAnchorRef = useRef<DOMElement | null>(null);
	const [viewportWidth, setViewportWidth] = useState(
		stdout?.columns ?? process.stdout.columns ?? 80
	);
	const controls = buildControls(draft.length > 0).join("  •  ");
	const footerLabel = `${
		activeAgentId ? `@${activeAgentId}` : "@no-agent"
	}  •  Stream: ${streamState}  •  Push: ${pushState}`;
	const lineWidth = Math.max(24, viewportWidth);
	const contentWidth = Math.max(1, lineWidth - 2);
	const draftLines =
		draft.length > 0 ? wrapComposerDraft(draft, contentWidth) : [""];
	const fillerRow = " ".repeat(lineWidth);
	const showAgentSuggestions = agentSuggestions.length > 0;
	const showFileSuggestions = fileSuggestions.length > 0;
	const showSlashList = slashMenuVisible && !slashSubmenu;
	const showFooter = !showAgentSuggestions && !showFileSuggestions && !slashMenuVisible && !slashSubmenu;
	const slashLabelWidth = getTableLabelWidth(slashCommands);
	const slashSubmenuLabelWidth = getTableLabelWidth(slashSubmenu?.options ?? [], true);
	const agentColumnBudget = Math.max(0, lineWidth - MENU_INDENT.length);
	const agentLabelWidth = getAgentSuggestionLabelWidth(agentSuggestions);
	const agentSourceWidth = getAgentSuggestionSourceWidth(agentSuggestions);
	const agentLabelColumnWidth = Math.min(
		agentLabelWidth + 2,
		Math.max(8, Math.floor(agentColumnBudget * 0.45))
	);
	const agentSourceColumnWidth = Math.min(
		agentSourceWidth + 2,
		Math.max(0, agentColumnBudget - agentLabelColumnWidth)
	);

	useComposerCursor(cursorAnchorRef, !slashSubmenu);

	useEffect(() => {
		const handleResize = (): void => {
			setViewportWidth(stdout?.columns ?? process.stdout.columns ?? 80);
		};

		handleResize();
		stdout?.on("resize", handleResize);

		return () => {
			stdout?.off("resize", handleResize);
		};
	}, [stdout]);

	return (
		<Box flexDirection="column" width={lineWidth}>
			<Box flexDirection="column">
				<Text backgroundColor={INPUT_BACKGROUND}>{fillerRow}</Text>
				{slashSubmenu ? (
					<>
						<Box>
							<Text backgroundColor={INPUT_BACKGROUND} color={INPUT_ACCENT}>
								{"  "}
							</Text>
							<Text backgroundColor={INPUT_BACKGROUND} color={INPUT_ACCENT}>
								{slashSubmenu.title}
							</Text>
							<Text backgroundColor={INPUT_BACKGROUND}>
								{" ".repeat(Math.max(0, contentWidth - slashSubmenu.title.length))}
							</Text>
						</Box>
						<Box>
							<Text backgroundColor={INPUT_BACKGROUND} color={INPUT_ACCENT}>
								{"  "}
							</Text>
							<Text backgroundColor={INPUT_BACKGROUND} color={SECONDARY_TEXT}>
								{slashSubmenu.subtitle}
							</Text>
							<Text backgroundColor={INPUT_BACKGROUND}>
								{" ".repeat(
									Math.max(0, contentWidth - slashSubmenu.subtitle.length)
								)}
							</Text>
						</Box>
						<Text backgroundColor={INPUT_BACKGROUND}>{fillerRow}</Text>
						{slashSubmenu.options.map((option, index) => {
							const label = `${index + 1}. ${option.label}`;
							const isSelected = index === slashSubmenu.selectedIndex;
							const color = isSelected ? SELECTION_HIGHLIGHT : PRIMARY_TEXT;
							const descriptionColor = isSelected
								? SELECTION_HIGHLIGHT
								: SECONDARY_TEXT;
							const paddedLabel = padLabel(label, slashSubmenuLabelWidth + 2);
							const remainingWidth = Math.max(
								0,
								contentWidth - paddedLabel.length - option.description.length
							);

							return (
								<Box key={option.label}>
									<Text backgroundColor={INPUT_BACKGROUND} color={INPUT_ACCENT}>
										{"  "}
									</Text>
									<Text backgroundColor={INPUT_BACKGROUND} color={color}>
										{paddedLabel}
									</Text>
									<Text backgroundColor={INPUT_BACKGROUND} color={descriptionColor}>
										{option.description}
									</Text>
									<Text backgroundColor={INPUT_BACKGROUND}>
										{" ".repeat(remainingWidth)}
									</Text>
								</Box>
							);
						})}
					</>
				) : draft.length > 0 ? (
					draftLines.map((line, index) => {
						const prefix = index === 0 ? "› " : "  ";
						const padding = " ".repeat(Math.max(0, contentWidth - line.length));
						const isCursorRow = index === draftLines.length - 1;
						return (
							<Box key={`draft-${index}`}>
								<Box ref={isCursorRow ? cursorAnchorRef : undefined}>
									<Text backgroundColor={INPUT_BACKGROUND} color={INPUT_PRIMARY}>
										{prefix}
									</Text>
									<Text
										backgroundColor={INPUT_BACKGROUND}
										color={INPUT_FOREGROUND}
									>
										{line}
									</Text>
								</Box>
								{padding.length > 0 ? (
									<Text backgroundColor={INPUT_BACKGROUND}>{padding}</Text>
								) : null}
							</Box>
						);
					})
				) : (
					<Box>
						<Box ref={cursorAnchorRef}>
							<Text backgroundColor={INPUT_BACKGROUND} color={INPUT_PRIMARY}>
								›{" "}
							</Text>
						</Box>
						<Text backgroundColor={INPUT_BACKGROUND} color={INPUT_PLACEHOLDER}>
							Send message
						</Text>
						<Text backgroundColor={INPUT_BACKGROUND}>
							{" ".repeat(Math.max(0, contentWidth - "Send message".length))}
						</Text>
					</Box>
				)}
				<Text backgroundColor={INPUT_BACKGROUND}>{fillerRow}</Text>
			</Box>
			{showAgentSuggestions ? (
				<Box flexDirection="column">
					<Text color={SECONDARY_TEXT}>Discovered: {discoveredCount}</Text>
					{agentSuggestions.map((suggestion, index) => {
						const isSelected = index === selectedSuggestionIndex;
						const label = formatColumn(`@${suggestion.id}`, agentLabelColumnWidth);
						const sourceName = formatColumn(
							suggestion.sourceName,
							agentSourceColumnWidth
						);
						const descriptionWidth = Math.max(
							0,
							agentColumnBudget - label.length - sourceName.length
						);
						const description = truncateText(
							suggestion.description,
							descriptionWidth
						);

						return (
							<Box key={suggestion.agentKey}>
								<Text color={isSelected ? SELECTION_HIGHLIGHT : PRIMARY_TEXT}>
									{isSelected ? "› " : MENU_INDENT}
								</Text>
								<Text color={isSelected ? SELECTION_HIGHLIGHT : PRIMARY_TEXT}>
									{label}
								</Text>
								<Text color={SECONDARY_TEXT}>{sourceName}</Text>
								<Text color={SECONDARY_TEXT}>{description}</Text>
							</Box>
						);
					})}
				</Box>
			) : null}
			{showFileSuggestions ? (
				<Box flexDirection="column">
					<Text color={SECONDARY_TEXT}>Files</Text>
					{fileSuggestions.map((label, index) => (
						<Text
							key={label}
							color={index === selectedFileSuggestionIndex ? SELECTION_HIGHLIGHT : PRIMARY_TEXT}
						>
							{index === selectedFileSuggestionIndex ? "› " : "  "}{label}
						</Text>
					))}
				</Box>
			) : null}
			{showSlashList ? (
				<Box flexDirection="column">
					{slashCommands.map((command, index) => {
						const isSelected = index === selectedSlashCommandIndex;
						const color = isSelected ? SELECTION_HIGHLIGHT : PRIMARY_TEXT;
						const descriptionColor = isSelected
							? SELECTION_HIGHLIGHT
							: SECONDARY_TEXT;
						const paddedLabel = padLabel(command.label, slashLabelWidth + 2);

						return (
							<Box key={command.label}>
								<Text color={PRIMARY_TEXT}>{MENU_INDENT}</Text>
								<Text color={color}>{paddedLabel}</Text>
								<Text color={descriptionColor}>{command.description}</Text>
							</Box>
						);
					})}
				</Box>
			) : null}
			{showFooter ? (
				<Box justifyContent="space-between">
					<Text color={SECONDARY_TEXT}>{footerLabel}</Text>
					<Text color={SECONDARY_TEXT}>{controls}</Text>
				</Box>
			) : null}
		</Box>
	);
}
