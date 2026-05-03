import {
	getGraphQLHttpUrl,
	getGraphQLWebSocketUrl,
	type AionEnvironmentId
} from "../environment.js";

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
	query: string;
	variables?: TVariables;
	accessToken?: string;
	fetchImpl?: typeof fetch;
	url?: string;
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

export async function executeGraphQL<TData, TVariables = Record<string, never>>(
	options: GraphQLRequestOptions<TVariables>
): Promise<GraphQLResponse<TData>> {
	const fetchImpl = options.fetchImpl ?? fetch;
	const headers = new Headers({
		"Content-Type": "application/json"
	});
	if (options.accessToken) {
		headers.set("Authorization", `Bearer ${options.accessToken}`);
	}

	const response = await fetchImpl(options.url ?? getGraphQLHttpUrl(options.environmentId), {
		method: "POST",
		headers,
		body: JSON.stringify({
			query: options.query,
			variables: options.variables ?? {}
		})
	});

	if (!response.ok) {
		throw new GraphQLRequestError(
			`GraphQL HTTP request failed: ${response.status} ${response.statusText}`,
			{ status: response.status }
		);
	}

	const payload = (await response.json()) as GraphQLResponse<TData>;
	if (payload.errors && payload.errors.length > 0) {
		throw new GraphQLRequestError(formatGraphQLErrors(payload.errors), {
			errors: payload.errors
		});
	}
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
