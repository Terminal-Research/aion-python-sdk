export type RequestMode = "send-message" | "streaming-message";
export type ResponseMode = "message-output" | "a2a-protocol";

export interface ChatModeSettings {
	requestMode: RequestMode;
	responseMode: ResponseMode;
}

export interface SlashCommandOption<TValue extends string> {
	value: TValue;
	label: string;
	description: string;
}

export interface SlashCommandDefinition<TValue extends string> {
	id: string;
	label: string;
	description: string;
	title: string;
	subtitle: string;
	options: readonly SlashCommandOption<TValue>[];
}

export const DEFAULT_CHAT_MODE_SETTINGS: ChatModeSettings = {
	requestMode: "send-message",
	responseMode: "message-output"
};

export const REQUEST_MODE_OPTIONS: readonly SlashCommandOption<RequestMode>[] = [
	{
		value: "send-message",
		label: "Send message",
		description: "Send a synchronous request and wait for a single reply."
	},
	{
		value: "streaming-message",
		label: "Streaming message",
		description: "Send a streaming request and render incremental events as they arrive."
	}
] as const;

export const RESPONSE_MODE_OPTIONS: readonly SlashCommandOption<ResponseMode>[] = [
	{
		value: "message-output",
		label: "Message output",
		description: "Render the agent message output instead of raw protocol events."
	},
	{
		value: "a2a-protocol",
		label: "A2A protocol",
		description: "Render raw A2A protocol payloads as YAML."
	}
] as const;

export const SLASH_COMMANDS = [
	{
		id: "clear",
		label: "/clear",
		description: "Clear the visible transcript and start fresh.",
		title: "Clear Transcript",
		subtitle: "Clear the visible transcript and start fresh.",
		options: []
	},
	{
		id: "login",
		label: "/login",
		description: "Authenticate Aion Chat with your Aion account.",
		title: "Login",
		subtitle: "Authenticate Aion Chat with your Aion account.",
		options: []
	},
	{
		id: "exit",
		label: "/exit",
		description: "Exit Aion Chat.",
		title: "Exit",
		subtitle: "Exit Aion Chat.",
		options: []
	},
	{
		id: "request",
		label: "/request",
		description: "Choose how Aion Chat sends requests to the agents.",
		title: "Request Mode",
		subtitle: "Choose how Aion Chat sends requests to the agents.",
		options: REQUEST_MODE_OPTIONS
	},
	{
		id: "response",
		label: "/response",
		description: "Choose how Aion Chat renders responses from the agents.",
		title: "Response Mode",
		subtitle: "Choose how Aion Chat renders responses from the agents.",
		options: RESPONSE_MODE_OPTIONS
	},
	{
		id: "sources",
		label: "/sources",
		description: "Show configured agent discovery sources.",
		title: "Agent Sources",
		subtitle: "Show configured agent discovery sources.",
		options: []
	}
] as const satisfies readonly SlashCommandDefinition<string>[];

export type SlashCommandId = (typeof SLASH_COMMANDS)[number]["id"];

export function getRequestModeLabel(mode: RequestMode): string {
	return REQUEST_MODE_OPTIONS.find((option) => option.value === mode)?.label ?? "Send message";
}

export function getResponseModeLabel(mode: ResponseMode): string {
	return (
		RESPONSE_MODE_OPTIONS.find((option) => option.value === mode)?.label ?? "Message output"
	);
}

export function getLeadingSlashQuery(draft: string): string | undefined {
	const trimmedStartIndex = draft.search(/\S/u);
	if (trimmedStartIndex === -1) {
		return undefined;
	}

	const trimmedDraft = draft.slice(trimmedStartIndex);
	if (!trimmedDraft.startsWith("/")) {
		return undefined;
	}

	return trimmedDraft.slice(1);
}

export function clearLeadingSlashDraft(draft: string): string {
	const trimmedStartIndex = draft.search(/\S/u);
	if (trimmedStartIndex === -1) {
		return "";
	}

	const trimmedDraft = draft.slice(trimmedStartIndex);
	if (!trimmedDraft.startsWith("/")) {
		return draft;
	}

	return draft.slice(0, trimmedStartIndex);
}

export function getSlashCommandById(
	commandId: SlashCommandId | undefined
): SlashCommandDefinition<string> | undefined {
	return SLASH_COMMANDS.find((command) => command.id === commandId);
}

export function filterSlashCommands(query: string | undefined): SlashCommandDefinition<string>[] {
	if (query === undefined) {
		return [];
	}

	const normalizedQuery = query.trim().toLowerCase();

	return [...SLASH_COMMANDS]
		.sort((left, right) => left.label.localeCompare(right.label))
		.filter((command) =>
			normalizedQuery.length === 0
				? true
				: command.label.slice(1).toLowerCase().startsWith(normalizedQuery)
		);
}
