import React from "react";
import { Box, Text } from "ink";

import { MarkdownBlock } from "../lib/markdown.js";

export interface TranscriptEntry {
	id: string;
	body: string;
	role: "agent" | "user" | "status" | "system" | "protocol";
}

function colorsForRole(role: TranscriptEntry["role"]): {
	borderColor: string;
	label: string;
	labelColor: string;
} {
	switch (role) {
		case "agent":
			return { borderColor: "green", label: "Agent", labelColor: "green" };
		case "user":
			return { borderColor: "cyan", label: "You", labelColor: "cyan" };
		case "status":
			return { borderColor: "yellow", label: "Status", labelColor: "yellow" };
		case "protocol":
			return { borderColor: "blue", label: "A2A", labelColor: "blue" };
		case "system":
		default:
			return { borderColor: "magenta", label: "System", labelColor: "magenta" };
	}
}

export function MessageBubble({ entry }: { entry: TranscriptEntry }): React.JSX.Element {
	const palette = colorsForRole(entry.role);

	return (
		<Box borderStyle="round" borderColor={palette.borderColor} paddingX={1} flexDirection="column">
			<Text color={palette.labelColor}>{palette.label}</Text>
			<MarkdownBlock content={entry.body} />
		</Box>
	);
}
