import React, { useEffect, useState } from "react";
import { Box, Text, useStdout } from "ink";

const INPUT_BACKGROUND = "#2A2F36";
const INPUT_FOREGROUND = "#F5F7FA";
const INPUT_PLACEHOLDER = "#C2C8D0";
const INPUT_ACCENT = "#FFFFFF";
const SECONDARY_TEXT = "#8B96A5";
const SELECTION_HIGHLIGHT = "green";

export interface ChatComposerProps {
	connected: boolean;
	draft: string;
	activeAgentId?: string;
	discoveredCount: number;
	pushState: string;
	streamState: string;
	agentSuggestions: string[];
	selectedSuggestionIndex: number;
}

function buildControls(hasDraft: boolean, hasSuggestions: boolean): string[] {
	const controls = hasSuggestions
		? ["Enter selects", "↑↓ move", "Esc cancels"]
		: ["Enter sends", "Shift+Enter newline"];

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

export function ChatComposer({
	connected,
	draft,
	activeAgentId,
	discoveredCount,
	pushState,
	streamState,
	agentSuggestions,
	selectedSuggestionIndex
}: ChatComposerProps): React.JSX.Element {
	const { stdout } = useStdout();
	const [viewportWidth, setViewportWidth] = useState(stdout?.columns ?? process.stdout.columns ?? 80);
	const controls = buildControls(draft.length > 0, agentSuggestions.length > 0).join("  •  ");
	const footerLabel = `${activeAgentId ? `@${activeAgentId}` : "@no-agent"}  •  Stream: ${streamState}  •  Push: ${pushState}`;
	const lineWidth = Math.max(24, viewportWidth);
	const contentWidth = Math.max(1, lineWidth - 2);
	const draftLines =
		draft.length > 0 ? wrapToWidth(draft, contentWidth) : [""];
	const fillerRow = " ".repeat(lineWidth);

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
				{draft.length > 0
					? draftLines.map((line, index) => {
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
					: (
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
			{agentSuggestions.length > 0 ? (
				<Box flexDirection="column">
					<Text color={SECONDARY_TEXT}>Discovered: {discoveredCount}</Text>
					{agentSuggestions.map((suggestion, index) => (
						<Text
							key={suggestion}
							color={index === selectedSuggestionIndex ? SELECTION_HIGHLIGHT : "gray"}
						>
							{index === selectedSuggestionIndex ? "› " : "  "}@{suggestion}
						</Text>
					))}
				</Box>
			) : null}
			{agentSuggestions.length === 0 ? (
				<Box justifyContent="space-between">
					<Text color={SECONDARY_TEXT}>{footerLabel}</Text>
					<Text color={SECONDARY_TEXT}>{controls}</Text>
				</Box>
			) : null}
		</Box>
	);
}
