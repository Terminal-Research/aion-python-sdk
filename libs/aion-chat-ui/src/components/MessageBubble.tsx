import React from "react";

import { AgentMessageBubble } from "./messages/AgentMessageBubble.js";
import { SystemMessageBubble } from "./messages/SystemMessageBubble.js";
import { UserMessageBubble } from "./messages/UserMessageBubble.js";
import { useTerminalWidth } from "./messages/messageLayout.js";
export { WorkingIndicator } from "./messages/WorkingIndicator.js";

export interface TranscriptEntry {
	id: string;
	body: string;
	role: "agent" | "user" | "status" | "system" | "protocol";
}

export function MessageBubble({ entry }: { entry: TranscriptEntry }): React.JSX.Element {
	const lineWidth = useTerminalWidth();

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
