import React, { useEffect, useState } from "react";
import { Box, Text, useStdout } from "ink";

import { SELECTION_HIGHLIGHT } from "../lib/slashCommands.js";

const INPUT_BACKGROUND = "#2A2F36";
const INPUT_FOREGROUND = "#F5F7FA";
const INPUT_PLACEHOLDER = "#C2C8D0";
const INPUT_ACCENT = "#FFFFFF";
const SECONDARY_TEXT = "#8B96A5";
const PRIMARY_TEXT = INPUT_FOREGROUND;
const MENU_INDENT = "  ";

export interface ComposerMenuItem {
	label: string;
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
	agentSuggestions: string[];
	selectedSuggestionIndex: number;
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

function padLabel(label: string, width: number): string {
	return `${label}${" ".repeat(Math.max(0, width - label.length))}`;
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

export function ChatComposer({
	draft,
	activeAgentId,
	discoveredCount,
	pushState,
	streamState,
	agentSuggestions,
	selectedSuggestionIndex,
	slashCommands,
	selectedSlashCommandIndex,
	slashMenuVisible,
	slashSubmenu
}: ChatComposerProps): React.JSX.Element {
	const { stdout } = useStdout();
	const [viewportWidth, setViewportWidth] = useState(
		stdout?.columns ?? process.stdout.columns ?? 80
	);
	const controls = buildControls(draft.length > 0).join("  •  ");
	const footerLabel = `${
		activeAgentId ? `@${activeAgentId}` : "@no-agent"
	}  •  Stream: ${streamState}  •  Push: ${pushState}`;
	const lineWidth = Math.max(24, viewportWidth);
	const contentWidth = Math.max(1, lineWidth - 2);
	const draftLines = draft.length > 0 ? wrapToWidth(draft, contentWidth) : [""];
	const fillerRow = " ".repeat(lineWidth);
	const showAgentSuggestions = agentSuggestions.length > 0;
	const showSlashList = slashMenuVisible && !slashSubmenu;
	const showFooter = !showAgentSuggestions && !slashMenuVisible && !slashSubmenu;
	const slashLabelWidth = getTableLabelWidth(slashCommands);
	const slashSubmenuLabelWidth = getTableLabelWidth(slashSubmenu?.options ?? [], true);

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
						return (
							<Box key={`draft-${index}`}>
								<Text backgroundColor={INPUT_BACKGROUND} color={INPUT_ACCENT}>
									{prefix}
								</Text>
								<Text backgroundColor={INPUT_BACKGROUND} color={INPUT_FOREGROUND}>
									{line}
								</Text>
								{padding.length > 0 ? (
									<Text backgroundColor={INPUT_BACKGROUND}>{padding}</Text>
								) : null}
							</Box>
						);
					})
				) : (
					<Box>
						<Text backgroundColor={INPUT_BACKGROUND} color={INPUT_ACCENT}>
							›{" "}
						</Text>
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
					{agentSuggestions.map((suggestion, index) => (
						<Text
							key={suggestion}
							color={index === selectedSuggestionIndex ? SELECTION_HIGHLIGHT : PRIMARY_TEXT}
						>
							{index === selectedSuggestionIndex ? "› " : "  "}@{suggestion}
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
