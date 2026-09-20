# Release checks

1. Compare the official index before deploy with the selected full fleet plugin set. Expected: all existing plugin names remain, with only Playbooks, Scheduler, DB, MCP, and Web Access moving to the tested versions.
2. Build the five source packages locally with the marketplace packager. Expected: each zip contains one package root, matching TOML/runtime versions, and a SHA-256 for the exact bytes.
3. Run marketplace packaging, seeding, manifest-version, and registry tests. Expected: all pass with immutable-version handling intact.
4. After main deploy, fetch `index.json` and all five artifact URLs. Expected: Playbooks 0.57.16, Scheduler 0.8.3, DB 0.1.1, MCP 0.2.1, and Web Access 0.3.1, with each downloaded artifact's SHA-256 equal to its index entry.
5. Confirm the other official plugin names and versions were not downgraded. Record the resulting hashes for fleet pinning.
