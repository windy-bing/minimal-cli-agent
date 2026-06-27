from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from minimal_cli_agent.skills import (
    build_system_prompt,
    detect_project_rule_conflicts,
    discover_project_rule_blocks,
    discover_project_rule_documents,
    discover_skill_paths,
    resolve_skill_path,
    resolve_skill_paths,
)


class SkillsTest(unittest.TestCase):
    def test_resolve_skill_by_name_under_workspace_skills(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill = root / "skills" / "demo"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("---\nname: demo\n---\n# Demo", encoding="utf-8")

            path = resolve_skill_path("demo", root)

        self.assertEqual(path.name, "SKILL.md")
        self.assertEqual(path.parent.name, "demo")

    def test_resolve_skill_paths_accepts_direct_directory(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill = root / "demo"
            skill.mkdir()
            (skill / "SKILL.md").write_text("# Demo", encoding="utf-8")

            paths = resolve_skill_paths(["demo"], root)

        self.assertEqual(len(paths), 1)

    def test_discover_skill_paths_lists_workspace_skills(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "skills" / "alpha"
            second = root / "skills" / "beta"
            first.mkdir(parents=True)
            second.mkdir(parents=True)
            (first / "SKILL.md").write_text("# Alpha", encoding="utf-8")
            (second / "SKILL.md").write_text("# Beta", encoding="utf-8")

            paths = discover_skill_paths(root)

        self.assertEqual([path.parent.name for path in paths], ["alpha", "beta"])

    def test_build_system_prompt_appends_skill_content(self) -> None:
        with TemporaryDirectory() as tmp:
            skill = Path(tmp) / "my-skill"
            skill.mkdir()
            path = skill / "SKILL.md"
            path.write_text("# Skill Body", encoding="utf-8")

            prompt = build_system_prompt("base", (path,))

        self.assertIn("base", prompt)
        self.assertIn('<skill name="my-skill"', prompt)
        self.assertIn("# Skill Body", prompt)

    def test_build_system_prompt_appends_project_rules_with_sources(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "AGENTS.md").write_text("Use focused tests.", encoding="utf-8")
            (root / ".agents").mkdir()
            (root / ".agents" / "rules.md").write_text("Use focused tests.", encoding="utf-8")

            prompt = build_system_prompt("base", (), root)

        self.assertIn("Project rules:", prompt)
        self.assertIn('<project_rules path="AGENTS.md" layer="project" precedence="10">', prompt)
        self.assertIn("Use focused tests.", prompt)
        self.assertEqual(prompt.count("Use focused tests."), 1)

    def test_project_rules_include_layered_rules_directory(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "AGENTS.md").write_text("Always run focused tests.", encoding="utf-8")
            rules_dir = root / ".agents" / "rules.d"
            rules_dir.mkdir(parents=True)
            (rules_dir / "python.md").write_text("Prefer unittest.", encoding="utf-8")

            documents = discover_project_rule_documents(root)

        self.assertEqual([document.relative_path for document in documents], ["AGENTS.md", ".agents/rules.d/python.md"])
        self.assertEqual(documents[1].layer, "rules.d")

    def test_project_rule_conflicts_are_reported(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "AGENTS.md").write_text("Always run focused tests.", encoding="utf-8")
            (root / ".minimal-agent-instructions.md").write_text("Never run focused tests.", encoding="utf-8")

            documents = discover_project_rule_documents(root)
            conflicts = detect_project_rule_conflicts(documents)
            blocks = discover_project_rule_blocks(root)

        self.assertEqual(conflicts[0].subject, "run focused tests")
        self.assertIn("<project_rule_conflicts>", blocks[0])
        self.assertIn("Always run focused tests.", blocks[0])
        self.assertIn("Never run focused tests.", blocks[0])

    def test_project_rules_are_truncated_to_budget(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "AGENTS.md").write_text("x" * 200, encoding="utf-8")

            blocks = discover_project_rule_blocks(root, max_chars=50)

        self.assertEqual(len(blocks), 1)
        self.assertIn("truncated by project rule budget", blocks[0])


if __name__ == "__main__":
    unittest.main()
