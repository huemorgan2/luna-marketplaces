# AUTO model selection — execution summary

The composer reads and persists AUTO/manual selection through the shared core model API. AUTO preserves the fallback chain, manual choices retain the existing chain-reorder behavior, and older cores hide the option. The plugin is versioned 0.30.0 with rebuilt assets.

Sixteen Vitest cases, the production build and a real desktop/mobile browser walkthrough passed. [Report and screenshots](../../tests/022-auto-model-selection/report.md).

The feature spans two repositories because the composer imports shared core components and APIs. Work used an isolated checkout to preserve unrelated edits. Release must pair this plugin with core 0.93.001; marketplace publication and production deployment remain pending.
