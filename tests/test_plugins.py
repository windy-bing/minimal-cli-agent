import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from minimal_cli_agent.constants import Tools
from minimal_cli_agent.harness import AgentHarness
from minimal_cli_agent.plugins import (
    discover_plugin_paths,
    load_plugin_manifest,
    load_plugin_mcp_configs,
    load_plugin_skill_paths,
    resolve_plugin_path,
)
from minimal_cli_agent.types import AgentConfig


class PluginsTest(unittest.TestCase):
    def test_load_plugin_manifest_resolves_relative_skills_and_mcp_configs(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = root / "plugins" / "demo"
            skill = plugin / "skills" / "demo"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("# Demo Skill", encoding="utf-8")
            (plugin / "mcp.json").write_text(
                json.dumps({"mcpServers": {"demo": {"url": "https://example.test/mcp"}}}),
                encoding="utf-8",
            )
            manifest_path = plugin / "plugin.json"
            manifest_path.write_text(
                json.dumps({"name": "demo", "skills": ["demo"], "mcp_configs": ["mcp.json"]}),
                encoding="utf-8",
            )

            manifest = load_plugin_manifest(manifest_path)

        self.assertEqual(manifest.name, "demo")
        self.assertEqual(manifest.skill_paths[0].name, "SKILL.md")
        self.assertEqual(manifest.mcp_config_paths[0].name, "mcp.json")

    def test_discover_and_resolve_plugin_paths(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = root / "plugins" / "demo"
            plugin.mkdir(parents=True)
            manifest_path = plugin / "plugin.json"
            manifest_path.write_text('{"name":"demo"}', encoding="utf-8")

            discovered = discover_plugin_paths(root, include_user=False)
            resolved = resolve_plugin_path("demo", root)

        self.assertEqual(discovered, (manifest_path.resolve(),))
        self.assertEqual(resolved, manifest_path.resolve())

    def test_plugin_mcp_configs_include_inline_servers(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = root / "plugins" / "demo"
            plugin.mkdir(parents=True)
            manifest_path = plugin / "plugin.json"
            manifest_path.write_text(
                json.dumps({"name": "demo", "mcpServers": {"coffee": {"url": "https://example.test/mcp"}}}),
                encoding="utf-8",
            )

            configs = load_plugin_mcp_configs((manifest_path,))

        self.assertEqual(configs[0].name, "coffee")
        self.assertEqual(configs[0].url, "https://example.test/mcp")

    def test_harness_registers_plugin_mcp_tools(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = root / "plugins" / "demo"
            plugin.mkdir(parents=True)
            manifest_path = plugin / "plugin.json"
            manifest_path.write_text(
                json.dumps({"name": "demo", "mcpServers": {"coffee": {"url": "https://example.test/mcp"}}}),
                encoding="utf-8",
            )

            harness = AgentHarness(AgentConfig(cwd=root, permission_mode="plan", plugin_paths=(manifest_path,)))

        self.assertIn(f"{Tools.MCP_PREFIX}coffee_list_tools", harness.tool_registry.available_names())

    def test_plugin_skill_paths_are_deduplicated(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = root / "plugins" / "demo"
            skill = plugin / "skills" / "demo"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("# Demo", encoding="utf-8")
            manifest_path = plugin / "plugin.json"
            manifest_path.write_text(
                json.dumps({"name": "demo", "skills": ["demo", "skills/demo/SKILL.md"]}),
                encoding="utf-8",
            )

            paths = load_plugin_skill_paths((manifest_path,))

        self.assertEqual(len(paths), 1)
        self.assertEqual(paths[0].name, "SKILL.md")


if __name__ == "__main__":
    unittest.main()
