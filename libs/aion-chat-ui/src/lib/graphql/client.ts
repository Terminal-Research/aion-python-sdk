import {
	getGraphQLHttpUrl,
	getGraphQLWebSocketUrl,
	type AionEnvironmentId
} from "../environment.js";
import {
	type ChatSessionLogger,
	sanitizeLogPayload
} from "../sessionLogger.js";

export interface GraphQLErrorPayload {
	message?: string;
	extensions?: Record<string, unknown>;
	path?: Array<string | number>;
}

export interface GraphQLResponse<TData> {
	data?: TData | null;
	errors?: GraphQLErrorPayload[];
}

export interface GraphQLRequestOptions<TVariables> {
	environmentId: AionEnvironmentId;
	operationName?: string;
	query: string;
	variables?: TVariables;
	accessToken?: string;
	fetchImpl?: typeof fetch;
	url?: string;
	logger?: ChatSessionLogger;
}

export class GraphQLRequestError extends Error {
	readonly status?: number;
	readonly errors?: GraphQLErrorPayload[];

	constructor(
		message: string,
		options: { status?: number; errors?: GraphQLErrorPayload[] } = {}
	) {
		super(message);
		this.name = "GraphQLRequestError";
		this.status = options.status;
		this.errors = options.errors;
	}
}

function formatGraphQLErrors(errors: GraphQLErrorPayload[]): string {
	const messages = errors
		.map((error) => error.message?.trim())
		.filter((message): message is string => Boolean(message));
	return messages.length > 0 ? messages.join("; ") : "GraphQL request failed";
}

function inferGraphQLOperationName(query: string): string {
	return (
		/\b(?:query|mutation|subscription)\s+([A-Za-z_][A-Za-z0-9_]*)/u.exec(
			query
		)?.[1] ?? "GraphQL"
	);
}

function summarizeGraphQLData(data: unknown): Record<string, unknown> {
	if (!data || typeof data !== "object" || Array.isArray(data)) {
		return { hasData: data !== null && data !== undefined };
	}

	return {
		hasData: true,
		dataKeys: Object.keys(data as Record<string, unknown>)
	};
}

function sanitizeGraphQLVariables<TVariables>(
	variables: TVariables | undefined
): Record<string, unknown> {
	if (!variables || typeof variables !== "object" || Array.isArray(variables)) {
		return {};
	}
	return sanitizeLogPayload(variables as Record<string, unknown>);
}

export async function executeGraphQL<TData, TVariables = Record<string, never>>(
	options: GraphQLRequestOptions<TVariables>
): Promise<GraphQLResponse<TData>> {
	const fetchImpl = options.fetchImpl ?? fetch;
	const url = options.url ?? getGraphQLHttpUrl(options.environmentId);
	const operationName =
		options.operationName ?? inferGraphQLOperationName(options.query);
	const startedAt = Date.now();
	const headers = new Headers({
		"Content-Type": "application/json"
	});
	if (options.accessToken) {
		headers.set("Authorization", `Bearer ${options.accessToken}`);
	}

	options.logger?.debug("graphql.request", {
		operationName,
		url,
		variables: sanitizeGraphQLVariables(options.variables)
	});

	let response: Response;
	try {
		response = await fetchImpl(url, {
			method: "POST",
			headers,
			body: JSON.stringify({
				query: options.query,
				variables: options.variables ?? {}
			})
		});
	} catch (error) {
		options.logger?.warn("graphql.request.failed", {
			operationName,
			url,
			durationMs: Date.now() - startedAt,
			error
		});
		throw error;
	}

	if (!response.ok) {
		options.logger?.warn("graphql.response.http_error", {
			operationName,
			url,
			status: response.status,
			statusText: response.statusText,
			durationMs: Date.now() - startedAt
		});
		throw new GraphQLRequestError(
			`GraphQL HTTP request failed: ${response.status} ${response.statusText}`,
			{ status: response.status }
		);
	}

	let payload: GraphQLResponse<TData>;
	try {
		payload = (await response.json()) as GraphQLResponse<TData>;
	} catch (error) {
		options.logger?.warn("graphql.response.invalid_json", {
			operationName,
			url,
			status: response.status,
			durationMs: Date.now() - startedAt,
			error
		});
		throw error;
	}

	if (payload.errors && payload.errors.length > 0) {
		options.logger?.warn("graphql.response.errors", {
			operationName,
			url,
			status: response.status,
			durationMs: Date.now() - startedAt,
			errors: payload.errors,
			...summarizeGraphQLData(payload.data)
		});
		throw new GraphQLRequestError(formatGraphQLErrors(payload.errors), {
			errors: payload.errors
		});
	}

	options.logger?.debug("graphql.response", {
		operationName,
		url,
		status: response.status,
		durationMs: Date.now() - startedAt,
		...summarizeGraphQLData(payload.data)
	});
	return payload;
}

export function buildAuthenticatedGraphQLWebSocketUrl(
	environmentId: AionEnvironmentId,
	accessToken?: string
): string {
	const url = new URL(getGraphQLWebSocketUrl(environmentId));
	if (accessToken) {
		url.searchParams.set("token", accessToken);
	}
	return url.toString();
}
