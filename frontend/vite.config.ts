import { sveltekit } from '@sveltejs/kit/vite';
import tailwindcss from '@tailwindcss/vite';
import { svelteTesting } from '@testing-library/svelte/vite';
import { defineConfig } from 'vitest/config';

export default defineConfig({
	plugins: [tailwindcss(), sveltekit(), svelteTesting()],
	server: {
		port: 5199,
	},
	test: {
		globals: true,
		environment: 'jsdom',
		setupFiles: ['./src/lib/test-setup.ts'],
		include: ['src/**/*.test.ts'],
		// CSS-raw audit-style tests (app.css.test.ts) read CSS file contents
		// via `import x from 'foo.css?raw'`. Vitest's default `vitest:css-
		// empty-post` plugin returns `export default ""` for any CSS request
		// when `test.css` is false/undefined; setting an include list scoped
		// to `?raw` short-circuits the stripper for that exact query without
		// processing CSS for any other test (which would have other side-
		// effects).
		css: {
			include: [/\.css\?raw/],
		},
		coverage: {
			provider: 'v8',
			include: ['src/lib/**/*.ts', 'src/lib/**/*.svelte'],
			exclude: [
				'**/*.test.ts',
				'**/test-*.ts',
				'src/lib/content/**',
				// WebGL / Three.js graphics — cannot run in jsdom (no GL context).
				// These are exercised via Playwright/manual QA on real GPUs.
				'src/lib/components/taxonomy/BeamPool.ts',
				'src/lib/components/taxonomy/BeamShader.ts',
				'src/lib/components/taxonomy/ClusterPhysics.ts',
				'src/lib/components/taxonomy/EdgeShader.ts',
				'src/lib/components/taxonomy/PlasmaBeam.ts',
				'src/lib/components/taxonomy/TopologyInteraction.ts',
				'src/lib/components/taxonomy/TopologyLabels.ts',
			],
			// Baseline established 2026-05-09. Lines-based threshold reflects the
			// true product-readiness coverage after WebGL/Three.js exclusions.
			// Re-raise to 85 once SemanticTopology + ForgeArtifact + Inspector
			// have dedicated render tests.
			thresholds: { lines: 78 },
		},
	},
});
