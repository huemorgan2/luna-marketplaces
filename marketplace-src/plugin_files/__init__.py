"""plugin-files — file storage and browser.

Provides agent tools for file management and a standalone file browser
UI served in an iframe via the 005.909 plugin architecture.
"""

from __future__ import annotations

import logging
from typing import Any

from luna_sdk import LunaPlugin, PluginContext, PluginManifest, SidebarSection, ToolDef

from .storage import make_storage_from_env

log = logging.getLogger("plugin-files")


class FilesPlugin(LunaPlugin):
    manifest = PluginManifest(
        name="plugin-files",
        version="0.2.1",
        description="File storage and browser.",
        category="system",
        sidebar_sections=[
            SidebarSection(id="files", label="Files", icon="folder", sort_order=35),
        ],
        routes_module="routes",
    )

    def __init__(self) -> None:
        self.storage: Any | None = None

    async def on_load(self, ctx: PluginContext) -> None:
        self.storage = make_storage_from_env()
        storage = self.storage

        async def _file_list(path: str = "/") -> dict[str, Any]:
            entries = await storage.list(path)
            return {
                "path": path,
                "entries": [
                    {
                        "path": e.path, "name": e.name, "is_dir": e.is_dir,
                        "size_bytes": e.size_bytes, "mime_type": e.mime_type,
                    }
                    for e in entries
                ],
                "count": len(entries),
            }

        async def _file_read(path: str) -> dict[str, Any]:
            try:
                entry = await storage.stat(path)
            except FileNotFoundError:
                return {"error": f"File not found: {path}"}
            if entry.is_dir:
                return {"error": f"Cannot read a directory: {path}"}
            if entry.size_bytes and entry.size_bytes > 100_000:
                return {
                    "path": path, "size_bytes": entry.size_bytes,
                    "mime_type": entry.mime_type,
                    "note": "File too large to return as text. Use the file browser UI.",
                }
            content = await storage.read(path)
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError:
                return {
                    "path": path, "size_bytes": len(content),
                    "mime_type": entry.mime_type,
                    "note": "Binary file. Use the file browser UI to preview.",
                }
            return {"path": path, "content": text, "size_bytes": len(content)}

        async def _file_write(path: str, content: str) -> dict[str, Any]:
            try:
                entry = await storage.write(path, content.encode("utf-8"))
            except ValueError as e:
                return {"error": str(e)}
            return {"written": True, "path": entry.path, "size_bytes": entry.size_bytes}

        async def _file_mkdir(path: str) -> dict[str, Any]:
            entry = await storage.mkdir(path)
            return {"created": True, "path": entry.path}

        async def _file_delete(path: str) -> dict[str, Any]:
            ok = await storage.delete(path)
            if not ok:
                return {"error": f"Not found: {path}"}
            return {"deleted": True, "path": path}

        async def _file_move(src: str, dst: str) -> dict[str, Any]:
            try:
                entry = await storage.move(src, dst)
            except FileNotFoundError:
                return {"error": f"Source not found: {src}"}
            return {"moved": True, "from": src, "to": entry.path}

        ctx.tool_registry.register(self.manifest.name, ToolDef(
            name="file_list", description="List files and folders in a directory.",
            parameters={"type": "object", "properties": {"path": {"type": "string", "description": "Directory path (default: /)"}}, "required": []},
            policy="auto_approve", risk_level="low",
        ), _file_list)

        ctx.tool_registry.register(self.manifest.name, ToolDef(
            name="file_read", description="Read a text file's content.",
            parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
            policy="auto_approve", risk_level="low",
        ), _file_read)

        ctx.tool_registry.register(self.manifest.name, ToolDef(
            name="file_write", description="Write text content to a file. Creates parent directories if needed.",
            parameters={"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]},
            policy="prompt_always", risk_level="low",
        ), _file_write)

        ctx.tool_registry.register(self.manifest.name, ToolDef(
            name="file_mkdir", description="Create a directory (and any parent directories).",
            parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
            policy="auto_approve", risk_level="low",
        ), _file_mkdir)

        ctx.tool_registry.register(self.manifest.name, ToolDef(
            name="file_delete", description="Delete a file or directory. Requires owner approval.",
            parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
            policy="prompt_always", risk_level="high",
        ), _file_delete)

        ctx.tool_registry.register(self.manifest.name, ToolDef(
            name="file_move", description="Move or rename a file or directory.",
            parameters={"type": "object", "properties": {"src": {"type": "string"}, "dst": {"type": "string"}}, "required": ["src", "dst"]},
            policy="prompt_always", risk_level="low",
        ), _file_move)

        log.info("plugin-files loaded (root=%s)", storage.root)
