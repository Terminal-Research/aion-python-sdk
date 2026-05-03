import type {
	LoginBootstrapQuery,
	LoginBootstrapQueryVariables,
	Route
} from "../../graphql/generated/graphql.js";
import {
	getWebAppBaseUrl,
	type AionEnvironmentId
} from "../environment.js";
import { executeGraphQL } from "./client.js";

export type NextRouteKind = "NONE" | "ONBOARDING" | "PATH";

export interface AuthBootstrapResult {
	nextRouteKind: NextRouteKind;
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

const ONBOARDING_ENTRY_PATH = "/onboarding/name";

function isValidWebAppPath(value: string | null | undefined): value is string {
	return Boolean(
		value &&
			value.startsWith("/") &&
			!value.startsWith("//") &&
			!/^[a-z][a-z0-9+.-]*:/iu.test(value)
	);
}

function normalizeRoute(
	nextRoute: Route | string | null | undefined
): Pick<AuthBootstrapResult, "nextRouteKind" | "nextRoutePath"> {
	if (typeof nextRoute !== "string") {
		return { nextRouteKind: "NONE", nextRoutePath: null };
	}

	const trimmed = nextRoute.trim();
	if (!trimmed) {
		return { nextRouteKind: "NONE", nextRoutePath: null };
	}

	const normalized = trimmed.toLowerCase();
	if (
		nextRoute === "Onboarding" ||
		normalized === "onboarding" ||
		normalized === "/onboarding" ||
		normalized.startsWith("/onboarding/")
	) {
		return { nextRouteKind: "ONBOARDING", nextRoutePath: null };
	}

	if (isValidWebAppPath(trimmed)) {
		return { nextRouteKind: "PATH", nextRoutePath: trimmed };
	}

	return { nextRouteKind: "NONE", nextRoutePath: null };
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
}): Promise<AuthBootstrapResult> {
	const response = await executeGraphQL<
		LoginBootstrapQuery,
		LoginBootstrapQueryVariables
	>({
		environmentId: options.environmentId,
		query: LOGIN_BOOTSTRAP_QUERY,
		variables: { token: options.accessToken },
		accessToken: options.accessToken,
		fetchImpl: options.fetchImpl,
		url: options.graphQLUrl
	});

	const login = response.data?.login ?? null;
	const route = normalizeRoute(login?.nextRoute);
	return {
		...route,
		loginEmail: normalizeOptionalString(login?.email),
		loginName: normalizeOptionalString(login?.name)
	};
}

export function resolvePostAuthPath(
	bootstrap: AuthBootstrapResult
): string | undefined {
	if (bootstrap.nextRouteKind === "ONBOARDING") {
		return ONBOARDING_ENTRY_PATH;
	}
	if (
		bootstrap.nextRouteKind === "PATH" &&
		isValidWebAppPath(bootstrap.nextRoutePath)
	) {
		return bootstrap.nextRoutePath;
	}
	return undefined;
}

export function buildWebAppRouteUrl(
	environmentId: AionEnvironmentId,
	pathname: string
): string {
	const url = new URL(getWebAppBaseUrl(environmentId));
	url.pathname = pathname;
	url.search = "";
	url.hash = "";
	return url.toString();
}
