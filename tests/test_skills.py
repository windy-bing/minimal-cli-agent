from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from minimal_cli_agent.skills import build_system_prompt, discover_skill_paths, resolve_skill_path, resolve_skill_paths


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


if __name__ == "__main__":
    unittest.main()
