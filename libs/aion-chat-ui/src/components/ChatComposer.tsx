import React, { useEffect, useState } from "react";
import { Box, Text, useStdout } from "ink";

export interface ChatComposerProps {
	connected: boolean;
	draft: string;
	activeAgentId?: string;
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

export function ChatComposer({
	connected,
	draft,
	activeAgentId,
	agentSuggestions,
	selectedSuggestionIndex
}: ChatComposerProps): React.JSX.Element {
	const { stdout } = useStdout();
	const [dividerWidth, setDividerWidth] = useState(stdout?.columns ?? process.stdout.columns ?? 80);
	const controls = buildControls(draft.length > 0, agentSuggestions.length > 0).join("  •  ");
	const agentLabel = activeAgentId ? `@${activeAgentId}` : "@no-agent";

	useEffect(() => {
		const handleResize = (): void => {
			setDividerWidth(stdout?.columns ?? process.stdout.columns ?? 80);
		};

		handleResize();
		stdout?.on("resize", handleResize);

		return () => {
			stdout?.off("resize", handleResize);
		};
	}, [stdout]);

	return (
		<Box flexDirection="column" paddingX={1}>
			<Text color={connected ? "gray" : "gray"}>
				{"─".repeat(Math.max(24, dividerWidth - 4))}
			</Text>
			<Box marginTop={1}>
				<Text color={connected ? "gray" : "gray"}>› </Text>
				<Text color={draft ? "white" : "gray"}>{draft || "Send message"}</Text>
			</Box>
			{agentSuggestions.length > 0 ? (
				<Box flexDirection="column" marginTop={1}>
					{agentSuggestions.map((suggestion, index) => (
						<Text
							key={suggestion}
							color={index === selectedSuggestionIndex ? "green" : "gray"}
						>
							{index === selectedSuggestionIndex ? "› " : "  "}@{suggestion}
						</Text>
					))}
				</Box>
			) : null}
			<Box justifyContent="space-between" marginTop={1}>
				<Text color={activeAgentId ? "green" : "gray"}>{agentLabel}</Text>
				<Text dimColor>{controls}</Text>
			</Box>
		</Box>
	);
}
