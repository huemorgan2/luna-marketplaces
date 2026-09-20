# AUTO model picker walkthrough

Use the actual ComposerModelSelect and shared ModelPickerMenu against controlled model API responses. This is a UI integration test, not evidence of model routing quality.

1. Load the local fixture with the backend in AUTO. Open the menu and verify AUTO is first, selected, and has explanatory text without invented rating scores or context-window controls. Save screenshot.
2. Choose GPT-4o. Verify the PUT contains `selection=manual` and a real model chain. Reload and verify GPT-4o remains selected.
3. Open the menu and choose AUTO. Verify a mode-only PUT with no AUTO entry in the chain. Reload and verify AUTO remains selected.
4. Repeat at a narrow viewport. Verify the menu stays inside the viewport and AUTO remains usable. Save screenshot.
5. Verify no unexpected browser errors. State that provider/model requests are mocked.

To reproduce, install the plugin UI and core `dojo` dependencies, then run
`VITEST=1 npm exec vite -- --host 127.0.0.1 --port 5187 --strictPort` from
`marketplace-src/plugin_chat_ui/ui-src`. In another terminal, run
`node tests/022-auto-model-selection/browser.mjs` from the marketplace root.
Set `LUNA_CORE_DIR` to a separate Luna checkout when the `luna` submodule is
not initialized. `AUTO_PICKER_URL` can override the fixture URL.
