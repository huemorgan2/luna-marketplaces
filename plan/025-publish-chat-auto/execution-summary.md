# 025 — Execution summary

Published the committed chat-ui 0.30.3 package through the official marketplace's existing Render seeding workflow. Deployment `dep-danrbcbtqb8s73csncog` is live at the exact tested marketplace commit 3ecc933, whose shared UI core pin is 887e2fb (Luna 0.92.058).

The live registry and downloaded artifact were verified. All five files and the complete archive match the deterministic local package; all 34 other plugin entries retain their versions and hashes. [Release report](../../tests/025-publish-chat-auto/report.md).

No publisher credentials or new infrastructure were needed: the authenticated Render CLI deployed the repository-owned official plugin. Hosted Luna upgrades, image pins and Jev secret provisioning remain separate from marketplace publication.
