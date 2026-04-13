export interface AgentMentionMatch {
	query: string;
	start: number;
	end: number;
}

export interface AgentSelectionResult {
	agentId?: string;
	message: string;
}

const AGENT_MENTION_PATTERN = /(?:^|\s)@([a-zA-Z0-9._-]*)$/;

export function getAgentMentionMatch(draft: string): AgentMentionMatch | undefined {
	const match = AGENT_MENTION_PATTERN.exec(draft);
	if (!match || match.index === undefined) {
		return undefined;
	}

	return {
		query: match[1] ?? "",
		start: match.index + match[0].lastIndexOf("@"),
		end: draft.length
	};
}

export function clearAgentMention(draft: string): string {
	const match = getAgentMentionMatch(draft);
	if (!match) {
		return draft;
	}

	return draft.slice(0, match.start).trimEnd();
}

export function parseAgentSelection(
	draft: string,
	availableAgentIds: string[]
): AgentSelectionResult {
	const trimmed = draft.trim();
	if (!trimmed.startsWith("@")) {
		return {
			message: trimmed
		};
	}

	const firstWhitespace = trimmed.search(/\s/);
	const mention = firstWhitespace === -1 ? trimmed : trimmed.slice(0, firstWhitespace);
	const message =
		firstWhitespace === -1 ? "" : trimmed.slice(firstWhitespace).trim();
	const agentId = mention.slice(1);

	if (!availableAgentIds.includes(agentId)) {
		return {
			message: trimmed
		};
	}

	return {
		agentId,
		message
	};
}
