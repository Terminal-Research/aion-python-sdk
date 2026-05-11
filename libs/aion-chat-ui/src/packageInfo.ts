import packageJson from "../package.json" with { type: "json" };

export interface PackageInfo {
	name: string;
	version: string;
	repositoryUrl?: string;
}

export function getPackageInfo(): PackageInfo {
	const repository = packageJson.repository as
		| string
		| { url?: string }
		| undefined;
	const repositoryUrl =
		typeof repository === "string" ? repository : repository?.url;

	return {
		name: packageJson.name,
		version: packageJson.version,
		...(repositoryUrl ? { repositoryUrl } : {})
	};
}
