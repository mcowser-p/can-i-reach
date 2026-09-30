// .releaserc.js
module.exports = {
  branches: ["main"],
  tagFormat: `v\${version}`,
  plugins: [
    [
      "@semantic-release/commit-analyzer",
      {
        preset: "angular",
        releaseRules: [
          // Ignore commits without <scope>
          { scope: null, release: false },
          { breaking: true, release: "major" },
          { type: "feat", release: "minor" },
          { type: "fix", release: "patch" },
          { type: "docs", release: "patch" },
          { type: "patch", release: "patch" },
          { type: "chore", release: false },
        ],
      },
    ],
    [
      // Renders the GitHub release body from the conventional commits in the
      // release. Without this plugin semantic-release emits an empty body, and
      // CHANGELOG.md points at the Releases page for the real notes.
      //
      // `types` must cover every type that releaseRules above can release on,
      // or that release ships with an empty body. The default angular preset
      // renders only feat/fix/perf, so a docs-only release — which
      // releaseRules makes a patch — comes out blank.
      //
      // Pinned to the conventionalcommits preset at v9, not v10: v10 requires
      // conventional-changelog-writer@9+, this plugin ships writer@8, and the
      // pair dies at generateNotes with a "Missing helper" error.
      "@semantic-release/release-notes-generator",
      {
        preset: "conventionalcommits",
        presetConfig: {
          types: [
            { type: "feat", section: "Features" },
            { type: "fix", section: "Bug Fixes" },
            { type: "docs", section: "Documentation" },
            { type: "patch", section: "Patches" },
            { type: "perf", section: "Performance" },
            { type: "chore", hidden: true },
            { type: "ci", hidden: true },
            { type: "test", hidden: true },
            { type: "refactor", hidden: true },
            { type: "style", hidden: true },
            { type: "build", hidden: true },
          ],
        },
      },
    ],
    [
      // Stamp the release version into galaxy.yml, build the collection
      // artifact, and publish it to Ansible Galaxy.
      "@semantic-release/exec",
      {
        prepareCmd:
          "sed -i 's/^version: .*/version: ${nextRelease.version}/' galaxy.yml && ansible-galaxy collection build --force",
        // Galaxy publish is optional: without the GALAXY_API_KEY secret the
        // GitHub release (tag + tarball) still happens and only the Galaxy
        // upload is skipped. A skipped version can be published later by
        // hand: ansible-galaxy collection publish <tarball> --token <key>.
        publishCmd:
          'if [ -n "$GALAXY_API_KEY" ]; then ansible-galaxy collection publish mcowser_p-can_i_reach-${nextRelease.version}.tar.gz --token "$GALAXY_API_KEY"; else echo "::warning::GALAXY_API_KEY not set — GitHub release only, Galaxy publish skipped"; fi',
      },
    ],
    [
      "@semantic-release/github",
      {
        successCommentCondition: false,
        failCommentCondition: false,
        assets: [
          { path: "mcowser_p-can_i_reach-*.tar.gz", label: "Ansible collection (mcowser_p.can_i_reach)" },
        ],
      },
    ],
  ],
};
