import { describe, expect, it, vi } from "vitest";

import {
	buildAuthenticatedGraphQLWebSocketUrl,
	executeGraphQL,
	GraphQLRequestError
} from "../src/lib/graphql/client.js";
import {
	buildWebAppRouteUrl,
	resolvePostAuthPath,
	runLoginBootstrap
} from "../src/lib/graphql/authBootstrap.js";
import { fetchRegistryAgentIdentities } from "../src/lib/graphql/registry.js";
import type {
	ChatSessionLogger,
	ChatSessionLogLevel
} from "../src/lib/sessionLogger.js";

interface LoggedEvent {
	level: ChatSessionLogLevel;
	event: string;
	data?: Record<string, unknown>;
}

function createMockSessionLogger(): ChatSessionLogger & { events: LoggedEvent[] } {
	const events: LoggedEvent[] = [];
	const log =
		(level: ChatSessionLogLevel) =>
		(event: string, data?: Record<string, unknown>): void => {
			events.push({ level, event, data });
		};
	return {
		chatSessionId: "test-session",
		logFilePath: "memory",
		level: "debug",
		debug: log("debug"),
		info: log("info"),
		warn: log("warn"),
		error: log("error"),
		flush: () => undefined,
		events
	};
}

function findLoggedEvent(
	logger: { events: LoggedEvent[] },
	event: string
): LoggedEvent | undefined {
	return logger.events.find((entry) => entry.event === event);
}

describe("GraphQL client", () => {
	it("posts authenticated GraphQL queries to the environment API", async () => {
		const fetchImpl = vi.fn(async () =>
			new Response(JSON.stringify({ data: { ok: true } }), { status: 200 })
		) as unknown as typeof fetch;

		await expect(
			executeGraphQL<{ ok: boolean }, { id: string }>({
				environmentId: "development",
				accessToken: "access-token",
				query: "query Test($id: ID!) { test(id: $id) }",
				variables: { id: "123" },
				fetchImpl
			})
		).resolves.toEqual({ data: { ok: true } });

		expect(fetchImpl).toHaveBeenCalledWith(
			"http://localhost:8080/api/graphql",
			expect.objectContaining({
				method: "POST",
				body: JSON.stringify({
					query: "query Test($id: ID!) { test(id: $id) }",
					variables: { id: "123" }
				})
			})
		);
		const headers = vi.mocked(fetchImpl).mock.calls[0]?.[1]?.headers as Headers;
		expect(headers.get("Authorization")).toBe("Bearer access-token");
	});

	it("throws graphQL errors even when partial data is returned", async () => {
		const fetchImpl = vi.fn(async () =>
			new Response(
				JSON.stringify({
					data: { login: null },
					errors: [{ message: "No access" }]
				}),
				{ status: 200 }
			)
		) as unknown as typeof fetch;

		await expect(
			executeGraphQL({
				environmentId: "development",
				query: "query Test { test }",
				fetchImpl
			})
		).rejects.toThrow(GraphQLRequestError);
	});

	it("adds the bearer token to GraphQL websocket URLs", () => {
		expect(
			buildAuthenticatedGraphQLWebSocketUrl("staging", "token-value")
		).toBe("wss://api-staging.aion.to/ws/graphql?token=token-value");
	});
});

describe("login bootstrap", () => {
	it("runs login bootstrap and uses the returned app path directly", async () => {
		const fetchImpl = vi.fn(async () =>
			new Response(
				JSON.stringify({
					data: {
						login: {
							nextRoute: " /onboarding?step=name ",
							email: " user@example.com ",
							name: " User Name "
						}
					}
				}),
				{ status: 200 }
			)
		) as unknown as typeof fetch;

		const bootstrap = await runLoginBootstrap({
			environmentId: "staging",
			accessToken: "access-token",
			fetchImpl
		});

		expect(bootstrap).toEqual({
			nextRoutePath: "/onboarding?step=name",
			loginEmail: "user@example.com",
			loginName: "User Name"
		});
		expect(resolvePostAuthPath(bootstrap)).toBe("/onboarding?step=name");
		expect(buildWebAppRouteUrl("staging", "/onboarding?step=name")).toBe(
			"https://app-staging.aion.to/onboarding?step=name"
		);

		const body = JSON.parse(
			String(vi.mocked(fetchImpl).mock.calls[0]?.[1]?.body)
		) as { variables: Record<string, unknown> };
		expect(body.variables).toEqual({ token: "access-token" });
	});

	it("falls back to the existing default when login does not return a route", async () => {
		const fetchImpl = vi.fn(async () =>
			new Response(
				JSON.stringify({
					data: {
						login: {
							nextRoute: null,
							email: null,
							name: null
						}
					}
				}),
				{ status: 200 }
			)
		) as unknown as typeof fetch;

		const bootstrap = await runLoginBootstrap({
			environmentId: "development",
			accessToken: "access-token",
			fetchImpl
		});

		expect(bootstrap).toEqual({
			nextRoutePath: null,
			loginEmail: null,
			loginName: null
		});
		expect(resolvePostAuthPath(bootstrap)).toBeUndefined();
	});

	it("does not navigate to external login routes", async () => {
		const fetchImpl = vi.fn(async () =>
			new Response(
				JSON.stringify({
					data: {
						login: {
							nextRoute: "https://example.com/onboarding",
							email: null,
							name: null
						}
					}
				}),
				{ status: 200 }
			)
		) as unknown as typeof fetch;

		const bootstrap = await runLoginBootstrap({
			environmentId: "production",
			accessToken: "access-token",
			fetchImpl
		});

		expect(bootstrap.nextRoutePath).toBeNull();
		expect(resolvePostAuthPath(bootstrap)).toBeUndefined();
		expect(() =>
			buildWebAppRouteUrl("production", "https://example.com/onboarding")
		).toThrow("Invalid Aion app route path.");
	});
});

describe("registry GraphQL", () => {
	it("loads current user and A2A agent identities", async () => {
		const fetchImpl = vi
			.fn()
			.mockResolvedValueOnce(
				new Response(
					JSON.stringify({
						data: {
							login: {
								nextRoute: null,
								email: null,
								name: null
							}
						}
					}),
					{ status: 200 }
				)
			)
			.mockResolvedValueOnce(
				new Response(
					JSON.stringify({
						data: {
							user: {
								id: "user-1",
								homeOrganization: { id: "org-1", name: "Org" },
								agentIdentity: {
									id: "personal-1",
									name: "Personal Agent",
									atName: "@me",
									a2aUrl: "https://agent.example/me",
									agentType: "Personal",
									organizationId: "org-1",
									updatedAt: "2026-05-01T00:00:00Z"
								}
							}
						}
					}),
					{ status: 200 }
				)
			)
			.mockResolvedValueOnce(
				new Response(
					JSON.stringify({
						data: {
							agentIdentityDetails: [
								{
									identity: {
										id: "principal-1",
										name: "Team Agent",
										atName: "@team",
										a2aUrl: "https://agent.example/team",
										agentType: "Principal",
										userId: null,
										organizationId: "org-1",
										systemKey: null,
										website: null,
										email: null,
										biography: null,
										avatarImageUrl: null,
										backgroundImageUrl: null,
										updatedAt: "2026-05-01T00:00:00Z",
										notes: null
									},
									distributionUsages: [
										{ distributionId: "dist-1", networkType: "A2A" }
									]
								}
							]
						}
					}),
					{ status: 200 }
				)
			) as unknown as typeof fetch;

		await expect(
			fetchRegistryAgentIdentities({
				environmentId: "development",
				accessToken: "access-token",
				fetchImpl
			})
		).resolves.toEqual([
			{
				id: "personal-1",
				name: "Personal Agent",
				atName: "@me",
				a2aUrl: "https://agent.example/me",
				updatedAt: "2026-05-01T00:00:00Z"
			},
			{
				id: "principal-1",
				name: "Team Agent",
				atName: "@team",
				a2aUrl: "https://agent.example/team",
				updatedAt: "2026-05-01T00:00:00Z"
			}
		]);
	});
	it("debug logs skipped catalog identities without raw profile fields", async () => {
		const logger = createMockSessionLogger();
		const fetchImpl = vi
			.fn()
			.mockResolvedValueOnce(
				new Response(
					JSON.stringify({
						data: {
							login: { nextRoute: null, email: null, name: null }
						}
					}),
					{ status: 200 }
				)
			)
			.mockResolvedValueOnce(
				new Response(
					JSON.stringify({
						data: {
							user: {
								id: "user-1",
								homeOrganization: { id: "org-1", name: "Org" },
								agentIdentity: {
									id: "personal-1",
									name: "Personal Agent",
									atName: "@me",
									a2aUrl: null,
									agentType: "Personal",
									organizationId: "org-1",
									updatedAt: "2026-05-01T00:00:00Z"
								}
							}
						}
					}),
					{ status: 200 }
				)
			)
			.mockResolvedValueOnce(
				new Response(
					JSON.stringify({
						data: {
							agentIdentityDetails: [
								{
									identity: {
										id: "principal-1",
										agentType: "Principal",
										userId: null,
										organizationId: "org-1",
										systemKey: null,
										name: "Team Agent",
										a2aUrl: null,
										website: null,
										email: "team@example.com",
										atName: "@team",
										biography: null,
										avatarImageUrl: null,
										backgroundImageUrl: null,
										updatedAt: "2026-05-01T00:00:00Z",
										notes: null
									},
									distributionUsages: [
										{ distributionId: "dist-1", networkType: "A2A" }
									]
								}
							]
						}
					}),
					{ status: 200 }
				)
			) as unknown as typeof fetch;

		await expect(
			fetchRegistryAgentIdentities({
				environmentId: "development",
				accessToken: "access-token",
				fetchImpl,
				logger
			})
		).resolves.toEqual([]);

		expect(
			findLoggedEvent(logger, "registry.agent_catalog.response")
		).toBeUndefined();
		const skippedEvents = logger.events.filter(
			(event) => event.event === "registry.agent_identity.skipped"
		);
		expect(skippedEvents).toHaveLength(2);
		expect(skippedEvents[1]).toMatchObject({
			level: "debug",
			data: {
				reason: "missing_a2a_url",
				source: "agent_catalog",
				identity: {
					id: "principal-1",
					name: "Team Agent",
					atName: "@team",
					agentType: "Principal",
					organizationId: "org-1"
				},
				distributionUsages: [
					{ distributionId: "dist-1", networkType: "A2A" }
				]
			}
		});
		const loggedJson = JSON.stringify(skippedEvents);
		expect(loggedJson).not.toContain("team@example.com");
		expect(loggedJson).not.toContain("biography");
		expect(loggedJson).not.toContain("notes");
		expect(loggedJson).not.toContain("website");
	});

});
