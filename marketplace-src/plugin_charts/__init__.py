"""plugin-charts — interactive Chart.js charts inline in the chat."""

from __future__ import annotations

import json
import logging
from typing import Any

from luna_sdk import LunaPlugin, PluginContext, PluginManifest, ToolDef

log = logging.getLogger("plugin-charts")


class ChartsPlugin(LunaPlugin):
    manifest = PluginManifest(
        name="plugin-charts",
        version="0.1.1",
        description="Interactive Chart.js charts rendered inline in the chat.",
    )

    async def on_load(self, ctx: PluginContext) -> None:
        from .renderer import render_chart

        async def _render_chart(
            type: str = "bar",
            title: str | None = None,
            labels: list[str] | None = None,
            datasets: list[dict[str, Any]] | None = None,
        ) -> str:
            if not labels or not datasets:
                return json.dumps({"error": "labels and datasets are required"})
            html = render_chart(
                chart_type=type,
                labels=labels,
                datasets=datasets,
                title=title,
            )
            return json.dumps({
                "chart_rendered": True,
                "type": type,
                "title": title,
                "embed_iframe": html,
            })

        ctx.tool_registry.register(
            self.manifest.name,
            ToolDef(
                name="render_chart",
                description=(
                    "Render an interactive chart inline in the chat. Use when the "
                    "owner asks for data visualization, comparisons, trends, or any "
                    "visual representation of numbers. Supports line, bar, pie, "
                    "doughnut, radar, and scatter charts. The chart appears directly "
                    "in the conversation with hover tooltips and animations."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "type": {
                            "type": "string",
                            "enum": ["line", "bar", "pie", "doughnut", "radar", "scatter"],
                            "description": "Chart type",
                        },
                        "title": {"type": "string", "description": "Chart title (optional)"},
                        "labels": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "X-axis labels or category names",
                        },
                        "datasets": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "label": {"type": "string", "description": "Dataset name"},
                                    "data": {"type": "array", "items": {"type": "number"}, "description": "Data values"},
                                    "color": {"type": "string", "description": "Hex color (optional)"},
                                },
                                "required": ["label", "data"],
                            },
                            "description": "One or more data series",
                        },
                    },
                    "required": ["type", "labels", "datasets"],
                },
                policy="auto_approve",
                risk_level="low",
            ),
            _render_chart,
        )
        log.info("charts.loaded")
