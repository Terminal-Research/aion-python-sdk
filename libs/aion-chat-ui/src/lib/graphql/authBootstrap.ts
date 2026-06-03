import type {
	LoginBootstrapQuery,
	LoginBootstrapQueryVariables
} from "../../graphql/generated/graphql.js";
import {
	getWebAppBaseUrl,
	type AionEnvironmentId
} from "../environment.js";
import type { ChatSessionLogger } from "../sessionLogger.js";
import { executeGraphQL } from "./client.js";

export interface AuthBootstrapResult {
	nextRoutePath: string | null;
	loginEmail: string | null;
	loginName: string | null;
}

export const LOGIN_BOOTSTRAP_QUERY = `
query LoginBootstrap($token: String!) {
	login(token: $token) {
		nextRoute
		email
		name
	}
}
`;

function isValidWebAppPath(value: string | null | undefined): value is string {
	return Boolean(
		value &&
			value.startsWith("/") &&
			!value.startsWith("//") &&
			!/^[a-z][a-z0-9+.-]*:/iu.test(value)
	);
}

function normalizeNextRoutePath(
	nextRoute: string | null | undefined
): Pick<AuthBootstrapResult, "nextRoutePath"> {
	if (typeof nextRoute !== "string") {
		return { nextRoutePath: null };
	}

	const trimmed = nextRoute.trim();
	if (!trimmed) {
		return { nextRoutePath: null };
	}

	if (isValidWebAppPath(trimmed)) {
		return { nextRoutePath: trimmed };
	}

	return { nextRoutePath: null };
}

function normalizeOptionalString(value: string | null | undefined): string | null {
	const trimmed = value?.trim();
	return trimmed ? trimmed : null;
}

export async function runLoginBootstrap(options: {
	environmentId: AionEnvironmentId;
	accessToken: string;
	fetchImpl?: typeof fetch;
	graphQLUrl?: string;
	logger?: ChatSessionLogger;
}): Promise<AuthBootstrapResult> {
	const response = await executeGraphQL<
		LoginBootstrapQuery,
		LoginBootstrapQueryVariables
	>({
		environmentId: options.environmentId,
		operationName: "LoginBootstrap",
		query: LOGIN_BOOTSTRAP_QUERY,
		variables: { token: options.accessToken },
		accessToken: options.accessToken,
		fetchImpl: options.fetchImpl,
		url: options.graphQLUrl,
		logger: options.logger
	});

	const login = response.data?.login ?? null;
	const route = normalizeNextRoutePath(login?.nextRoute);
	return {
		...route,
		loginEmail: normalizeOptionalString(login?.email),
		loginName: normalizeOptionalString(login?.name)
	};
}

export function resolvePostAuthPath(
	bootstrap: AuthBootstrapResult
): string | undefined {
	if (isValidWebAppPath(bootstrap.nextRoutePath)) {
		return bootstrap.nextRoutePath;
	}
	return undefined;
}

export function buildWebAppRouteUrl(
	environmentId: AionEnvironmentId,
	pathname: string
): string {
	if (!isValidWebAppPath(pathname)) {
		throw new Error("Invalid Aion app route path.");
	}

	const baseUrl = new URL(getWebAppBaseUrl(environmentId));
	const url = new URL(pathname, baseUrl);
	if (url.origin !== baseUrl.origin) {
		throw new Error("Invalid Aion app route path.");
	}
	return url.toString();
}
