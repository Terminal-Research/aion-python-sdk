import React from "react";
import { Text } from "ink";

import { AgentMessageBubble } from "./messages/AgentMessageBubble.js";
import { SystemMessageBubble } from "./messages/SystemMessageBubble.js";
import { UserMessageBubble } from "./messages/UserMessageBubble.js";
import { MESSAGE_THEME } from "../lib/theme.js";
import type { TranscriptEntry } from "../lib/transcript.js";
import { useTerminalWidth } from "./messages/messageLayout.js";
export { WorkingIndicator } from "./messages/WorkingIndicator.js";
export type { TranscriptEntry } from "../lib/transcript.js";

export function MessageBubble({ entry }: { entry: TranscriptEntry }): React.JSX.Element {
	const lineWidth = useTerminalWidth();

	if (entry.role === "divider") {
		return (
			<Text color={MESSAGE_THEME.muted}>
				{"-".repeat(lineWidth)}
			</Text>
		);
	}

	if (entry.role === "user") {
		return <UserMessageBubble body={entry.body} lineWidth={lineWidth} />;
	}

	if (entry.role === "agent") {
		return <AgentMessageBubble body={entry.body} lineWidth={lineWidth} />;
	}

	return (
		<SystemMessageBubble
			body={entry.body}
			kind={entry.role}
			lineWidth={lineWidth}
		/>
	);
}
