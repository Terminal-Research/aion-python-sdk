import { fileURLToPath } from "node:url";

import { defineConfig } from "tsup";

const reactDevtoolsShim = fileURLToPath(
	new URL("./src/shims/react-devtools-core.ts", import.meta.url)
);

export default defineConfig({
	entry: ["src/cli.tsx"],
	format: ["esm"],
	platform: "node",
	target: "node22",
	outDir: "dist",
	clean: true,
	splitting: false,
	skipNodeModulesBundle: false,
	noExternal: [/.*/],
	outExtension() {
		return {
			js: ".mjs"
		};
	},
	esbuildOptions(options) {
		options.banner = {
			js: 'import { createRequire as __createRequire } from "node:module"; const require = __createRequire(import.meta.url);'
		};
		options.alias = {
			...(options.alias ?? {}),
			"react-devtools-core": reactDevtoolsShim
		};
	}
});
