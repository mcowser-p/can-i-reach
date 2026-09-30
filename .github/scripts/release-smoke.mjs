// Pre-merge smoke test for the release toolchain.
//
// Dependabot bumps semantic-release and its plugins, but nothing exercised
// them before merge: a broken plugin or preset only surfaced when the
// Release workflow ran on main. This loads the real .releaserc.js, drops
// the plugins that need write access (github, exec), makes every commit
// releasable so generateNotes actually renders with the configured
// preset, and runs semantic-release in dry-run against the PR branch.
//
// It is hermetic: semantic-release insists on verifying push access
// (`git push --dry-run`) even in dry-run mode, and a PR workflow token is
// read-only, so the checkout is cloned into a temporary bare mirror and
// that mirror is the "remote" — no network, no credentials.
//
//   SMOKE_BRANCH=<branch> node .github/scripts/release-smoke.mjs
import semanticRelease from "semantic-release";
import { execFileSync } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { join } from "node:path";

const require = createRequire(import.meta.url);
const config = require(`${process.cwd()}/.releaserc.js`);
const pluginName = (p) => (Array.isArray(p) ? p[0] : p);
const pluginOpts = (p) => (Array.isArray(p) ? p[1] ?? {} : {});
const skipped = ["@semantic-release/github", "@semantic-release/exec"];

const plugins = config.plugins
  .filter((p) => !skipped.includes(pluginName(p)))
  .map((p) =>
    pluginName(p) === "@semantic-release/commit-analyzer"
      ? [pluginName(p), { ...pluginOpts(p), releaseRules: [{ release: "patch" }] }]
      : p,
  );

const branch = process.env.SMOKE_BRANCH || "main";
const mirror = join(mkdtempSync(join(tmpdir(), "release-smoke-")), "remote.git");
execFileSync("git", ["clone", "--quiet", "--bare", process.cwd(), mirror], { stdio: "inherit" });

const result = await semanticRelease(
  { ...config, plugins, branches: [branch], repositoryUrl: mirror, dryRun: true, ci: false },
  { env: { ...process.env, GITHUB_ACTIONS: "" } },
);

if (!result) {
  console.error(`release-smoke: no dry-run release on '${branch}' — generateNotes was not exercised`);
  process.exit(1);
}
const { version, type, notes } = result.nextRelease;
if (!notes || notes.length === 0) {
  console.error("release-smoke: generateNotes rendered empty notes");
  process.exit(1);
}
console.log(`release-smoke: ok — would cut ${version} (${type}), notes rendered (${notes.length} chars)`);
