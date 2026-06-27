from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from minimal_cli_agent.skills import build_system_prompt, resolve_skill_path, resolve_skill_paths


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
