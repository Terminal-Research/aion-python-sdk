import React from "react";
import { Box, Text } from "ink";

import { MESSAGE_THEME } from "../../lib/theme.js";
import { wrapToWidth } from "./messageLayout.js";

export type SystemMessageKind = "system" | "status" | "protocol";

const SYSTEM_COMMAND_HIGHLIGHT = "/sources";

function labelForKind(kind: SystemMessageKind): string {
	switch (kind) {
		case "status":
			return "Status";
		case "protocol":
			return "Protocol";
		case "system":
		default:
			return "System";
	}
}

function renderSystemLine(line: string): React.JSX.Element {
	if (!line.includes(SYSTEM_COMMAND_HIGHLIGHT)) {
		return <Text color={MESSAGE_THEME.foreground}>{line}</Text>;
	}

	const parts = line.split(SYSTEM_COMMAND_HIGHLIGHT);
	return (
		<>
			{parts.map((part, index) => (
				<React.Fragment key={`${part}-${index}`}>
					{index > 0 ? (
						<Text color={MESSAGE_THEME.primary}>{SYSTEM_COMMAND_HIGHLIGHT}</Text>
					) : null}
					{part ? <Text color={MESSAGE_THEME.foreground}>{part}</Text> : null}
				</React.Fragment>
			))}
		</>
	);
}

export function SystemMessageBubble({
	body,
	kind,
	lineWidth
}: {
	body: string;
	kind: SystemMessageKind;
	lineWidth: number;
}): React.JSX.Element {
	const marker = `· ${labelForKind(kind)} `;
	const markerWidth = marker.length;
	const contentWidth = Math.max(1, lineWidth - markerWidth);
	const lines = wrapToWidth(body, contentWidth);

	return (
		<Box flexDirection="column" width={lineWidth}>
			{lines.map((line, index) => (
				<Box key={`${kind}-${index}`}>
					{index === 0 ? (
						<Text color={MESSAGE_THEME.labelAccent}>{marker}</Text>
					) : (
						<Text>{" ".repeat(markerWidth)}</Text>
					)}
					{renderSystemLine(line)}
				</Box>
			))}
		</Box>
	);
}
