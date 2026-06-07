import React from "react";
import { Box, Text } from "ink";

import { MESSAGE_THEME } from "../../lib/theme.js";
import { wrapToWidth } from "./messageLayout.js";

export type SystemMessageKind = "system" | "status" | "protocol";

const SYSTEM_COMMAND_HIGHLIGHTS = ["/sources", "/clear"];

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
	const pattern = new RegExp(
		`(${SYSTEM_COMMAND_HIGHLIGHTS.map((command) => command.replace("/", "\\/"))
			.join("|")})`,
		"gu"
	);
	const parts = line.split(pattern).filter((part) => part.length > 0);
	if (parts.length === 1 && parts[0] === line) {
		return <Text color={MESSAGE_THEME.foreground}>{line}</Text>;
	}

	return (
		<>
			{parts.map((part, index) => (
				<Text
					key={`${part}-${index}`}
					color={
						SYSTEM_COMMAND_HIGHLIGHTS.includes(part)
							? MESSAGE_THEME.primary
							: MESSAGE_THEME.foreground
					}
				>
					{part}
				</Text>
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
