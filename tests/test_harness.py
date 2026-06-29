import unittest
import json
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from minimal_cli_agent.constants import EventKinds, Tools
from minimal_cli_agent.harness import AgentHarness, Observation, bucket_tool_calls, canonical_payload
from minimal_cli_agent.memory import JsonSessionStore
from minimal_cli_agent.types import AgentConfig, CommandResult, ToolCall


class SlowReadHarness(AgentHarness):
    def execute_tool(self, call: ToolCall) -> Observation:
        time.sleep(0.15)
        return Observation(
            action=call.name,
            payload=call.payload,
            result=CommandResult(call.name, 0, f"done:{call.payload}"),
        )


class FailingReadHarness(AgentHarness):
    def execute_tool(self, call: ToolCall) -> Observation:
        if call.payload == "boom":
            raise RuntimeError("boom")
        return Observation(
            action=call.name,
            payload=call.payload,
            result=CommandResult(call.name, 0, "ok"),
        )


class HarnessTest(unittest.TestCase):
    def test_plan_shell_uses_tool_pipeline_boundary(self) -> None:
        config = AgentConfig(permission_mode="plan")
        harness = AgentHarness(config)

        observation = harness.execute_shell("echo hello")

        self.assertEqual(observation.action, "shell")
        self.assertTrue(observation.result.skipped)
        self.assertIn("plan mode", observation.to_message().content)

    def test_auto_edit_can_write_and_read_workspace_file(self) -> None:
        with TemporaryDirectory() as tmp:
            config = AgentConfig(cwd=Path(tmp), permission_mode="autoEdit")
            harness = AgentHarness(config)

            write = harness.execute_tool(
                ToolCall(
                    name=Tools.WRITE_FILE,
                    payload=json.dumps({"path": "notes/todo.txt", "content": "hello"}),
                )
            )
            read = harness.execute_tool(ToolCall(name=Tools.READ_FILE, payload=json.dumps({"path": "notes/todo.txt"})))

        self.assertEqual(write.result.exit_code, 0)
        self.assertIn("Wrote notes/todo.txt", write.result.output)
        self.assertEqual(read.result.output, "hello")

    def test_plan_mode_skips_workspace_writes(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "notes.txt"
            harness = AgentHarness(AgentConfig(cwd=Path(tmp), permission_mode="plan"))

            observation = harness.execute_tool(
                ToolCall(name=Tools.WRITE_FILE, payload=json.dumps({"path": "notes.txt", "content": "hello"}))
            )

            self.assertFalse(path.exists())

        self.assertTrue(observation.result.skipped)
        self.assertIn("plan mode", observation.result.output)

    def test_read_tail_reads_bounded_last_lines(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "large.txt"
            path.write_text("\n".join(f"line-{index}" for index in range(200)), encoding="utf-8")
            harness = AgentHarness(AgentConfig(cwd=Path(tmp), permission_mode="plan"))

            observation = harness.execute_tool(
                ToolCall(name=Tools.READ_TAIL, payload=json.dumps({"path": "large.txt", "lines": 3}))
            )

        self.assertEqual(observation.result.output, "line-197\nline-198\nline-199")

    def test_read_forward_reads_bounded_range(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "page.txt"
            path.write_text("abcdef", encoding="utf-8")
            harness = AgentHarness(AgentConfig(cwd=Path(tmp), permission_mode="plan"))

            observation = harness.execute_tool(
                ToolCall(name=Tools.READ_FORWARD, payload=json.dumps({"path": "page.txt", "offset": 2, "limit": 3}))
            )

        self.assertEqual(observation.result.output, "cde")

    def test_bucket_tool_calls_groups_reads_around_write_barriers(self) -> None:
        calls = [
            ToolCall(name=Tools.READ_FILE, payload='{"path":"a.txt"}'),
            ToolCall(name=Tools.READ_TAIL, payload='{"path":"a.txt"}'),
            ToolCall(name=Tools.WRITE_FILE, payload='{"path":"a.txt","content":"x"}'),
            ToolCall(name=Tools.SEARCH, payload='{"pattern":"x","path":"."}'),
        ]

        buckets = bucket_tool_calls(calls)

        self.assertEqual(
            [[call.name for call in bucket] for bucket in buckets],
            [["read_file", "read_tail"], ["write_file"], ["search"]],
        )

    def test_execute_tools_runs_read_batches_and_preserves_order(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.txt").write_text("alpha", encoding="utf-8")
            (root / "b.txt").write_text("beta", encoding="utf-8")
            harness = AgentHarness(AgentConfig(cwd=root, permission_mode="plan"))

            observations = harness.execute_tools(
                [
                    ToolCall(name="read", payload=json.dumps({"path": "a.txt"})),
                    ToolCall(name=Tools.READ_FILE, payload=json.dumps({"path": "b.txt"})),
                ]
            )

        self.assertEqual([observation.action for observation in observations], ["read", "read_file"])
        self.assertEqual([observation.result.output for observation in observations], ["alpha", "beta"])

    def test_consolidate_tool_calls_deduplicates_identical_read_only_calls(self) -> None:
        harness = AgentHarness(AgentConfig(permission_mode="plan"))
        calls = [
            ToolCall(name="read", payload=json.dumps({"path": "a.txt"})),
            ToolCall(name=Tools.READ_FILE, payload=json.dumps({"path": "a.txt"})),
            ToolCall(name=Tools.READ_FORWARD, payload=json.dumps({"path": "a.txt", "offset": 0, "limit": 10})),
            ToolCall(name=Tools.READ_FORWARD, payload=json.dumps({"limit": 10, "offset": 0, "path": "a.txt"})),
            ToolCall(name=Tools.WRITE_FILE, payload=json.dumps({"path": "a.txt", "content": "x"})),
            ToolCall(name=Tools.WRITE_FILE, payload=json.dumps({"path": "a.txt", "content": "x"})),
        ]

        consolidated = harness.consolidate_tool_calls(calls)

        self.assertEqual([call.name for call in consolidated], ["read", Tools.READ_FORWARD, Tools.WRITE_FILE, Tools.WRITE_FILE])

    def test_canonical_payload_normalizes_json_key_order(self) -> None:
        left = canonical_payload('{"path":"a.txt","offset":0,"limit":10}')
        right = canonical_payload('{"limit":10,"offset":0,"path":"a.txt"}')

        self.assertEqual(left, right)

    def test_execute_tools_runs_parallel_safe_reads_concurrently(self) -> None:
        harness = SlowReadHarness(AgentConfig(permission_mode="plan"))

        started = time.monotonic()
        observations = harness.execute_tools(
            [
                ToolCall(name=Tools.READ_FILE, payload="a"),
                ToolCall(name=Tools.READ_TAIL, payload="b"),
            ]
        )
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 0.27)
        self.assertEqual([observation.action for observation in observations], [Tools.READ_FILE, Tools.READ_TAIL])

    def test_execute_tools_preserves_original_parallel_exception(self) -> None:
        harness = FailingReadHarness(AgentConfig(permission_mode="plan"))

        with self.assertRaisesRegex(RuntimeError, "boom"):
            harness.execute_tools(
                [
                    ToolCall(name=Tools.READ_FILE, payload="ok"),
                    ToolCall(name=Tools.READ_TAIL, payload="boom"),
                ]
            )

    def test_execute_tools_records_batch_metrics(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.txt").write_text("alpha", encoding="utf-8")
            (root / "b.txt").write_text("beta", encoding="utf-8")
            store = JsonSessionStore(root / "session.json")
            harness = AgentHarness(AgentConfig(cwd=root, permission_mode="plan"), session_store=store)
            harness.trace_id = "trace-123"

            harness.execute_tools(
                [
                    ToolCall(name=Tools.READ_FILE, payload=json.dumps({"path": "a.txt"})),
                    ToolCall(name=Tools.READ_FILE, payload=json.dumps({"path": "b.txt"})),
                ]
            )
            events = store.query_events(kind=EventKinds.TOOL_BATCH, limit=5)

        self.assertEqual(len(events), 1)
        self.assertTrue(events[0].data["parallel"])
        self.assertEqual(events[0].data["status"], "ok")
        self.assertEqual(events[0].data["actions"], [Tools.READ_FILE, Tools.READ_FILE])
        self.assertEqual(events[0].data["trace_id"], "trace-123")

    def test_execute_tools_records_parallel_error_metrics(self) -> None:
        with TemporaryDirectory() as tmp:
            store = JsonSessionStore(Path(tmp) / "session.json")
            harness = FailingReadHarness(AgentConfig(permission_mode="plan"), session_store=store)

            with self.assertRaisesRegex(RuntimeError, "boom"):
                harness.execute_tools(
                    [
                        ToolCall(name=Tools.READ_FILE, payload="ok"),
                        ToolCall(name=Tools.READ_TAIL, payload="boom"),
                    ]
                )
            events = store.query_events(kind=EventKinds.TOOL_BATCH, limit=5)

        self.assertEqual(len(events), 1)
        self.assertTrue(events[0].data["parallel"])
        self.assertEqual(events[0].data["status"], "error")
        self.assertIn("boom", events[0].data["error"])

    def test_read_forward_line_mode_returns_paging_metadata(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "page.txt"
            path.write_text("line-0\nline-1\nline-2\nline-3\n", encoding="utf-8")
            harness = AgentHarness(AgentConfig(cwd=Path(tmp), permission_mode="plan"))

            observation = harness.execute_tool(
                ToolCall(
                    name=Tools.READ_FORWARD,
                    payload=json.dumps({"path": "page.txt", "mode": "lines", "line_offset": 1, "line_limit": 2}),
                )
            )

        self.assertEqual(observation.result.output, "line-1\nline-2\n")
        self.assertEqual(observation.result.metadata["mode"], "lines")
        self.assertEqual(observation.result.metadata["next_line_offset"], 3)
        self.assertFalse(observation.result.metadata["eof"])

    def test_read_forward_rejects_line_offset_when_mode_is_bytes(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "page.txt"
            path.write_text("line-0\nline-1\n", encoding="utf-8")
            harness = AgentHarness(AgentConfig(cwd=Path(tmp), permission_mode="plan"))

            observation = harness.execute_tool(
                ToolCall(
                    name=Tools.READ_FORWARD,
                    payload=json.dumps({"path": "page.txt", "mode": "bytes", "line_offset": 1}),
                )
            )

        self.assertEqual(observation.result.exit_code, 1)
        self.assertIn('mode "lines"', observation.result.output)

    def test_read_file_rejects_binary_content(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "image.bin"
            path.write_bytes(b"abc\x00def")
            harness = AgentHarness(AgentConfig(cwd=Path(tmp), permission_mode="plan"))

            observation = harness.execute_tool(ToolCall(name=Tools.READ_FILE, payload=json.dumps({"path": "image.bin"})))

        self.assertEqual(observation.result.exit_code, 1)
        self.assertIn("binary", observation.result.output)

    def test_file_info_summarizes_binary_file(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "image.bin"
            path.write_bytes(b"abc\x00def")
            harness = AgentHarness(AgentConfig(cwd=Path(tmp), permission_mode="plan"))

            observation = harness.execute_tool(ToolCall(name=Tools.FILE_INFO, payload=json.dumps({"path": "image.bin"})))

        data = json.loads(observation.result.output)
        self.assertEqual(observation.result.exit_code, 0)
        self.assertTrue(data["is_binary"])
        self.assertEqual(data["path"], "image.bin")
        self.assertRegex(data["sha256"], r"^[0-9a-f]{64}$")
        self.assertIn("hex_preview", data)

    def test_search_returns_top_k_matches(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.txt").write_text("needle one\nmiss\nneedle two\n", encoding="utf-8")
            (root / "b.txt").write_text("needle three\nneedle four\n", encoding="utf-8")
            harness = AgentHarness(AgentConfig(cwd=root, permission_mode="plan"))

            observation = harness.execute_tool(
                ToolCall(name=Tools.SEARCH, payload=json.dumps({"pattern": "needle", "path": ".", "top_k": 2}))
            )

        self.assertIn("a.txt:1: needle one", observation.result.output)
        self.assertIn("a.txt:3: needle two", observation.result.output)
        self.assertNotIn("needle three", observation.result.output)

    def test_search_ranks_filename_matches_before_earlier_files(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "aaa.txt").write_text("needle low score\n", encoding="utf-8")
            (root / "needle_notes.txt").write_text("needle high score\n", encoding="utf-8")
            harness = AgentHarness(AgentConfig(cwd=root, permission_mode="plan"))

            observation = harness.execute_tool(
                ToolCall(name=Tools.SEARCH, payload=json.dumps({"pattern": "needle", "path": ".", "top_k": 1}))
            )

        self.assertIn("needle_notes.txt:1: needle high score", observation.result.output)
        self.assertNotIn("aaa.txt", observation.result.output)

    def test_search_respects_extra_ignore_dirs(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "visible.txt").write_text("needle visible\n", encoding="utf-8")
            ignored = root / "dist"
            ignored.mkdir()
            (ignored / "hidden.txt").write_text("needle hidden\n", encoding="utf-8")
            harness = AgentHarness(AgentConfig(cwd=root, permission_mode="plan"))

            observation = harness.execute_tool(
                ToolCall(
                    name=Tools.SEARCH,
                    payload=json.dumps({"pattern": "needle", "path": ".", "ignore_dirs": ["dist"]}),
                )
            )

        self.assertIn("visible.txt:1: needle visible", observation.result.output)
        self.assertNotIn("hidden", observation.result.output)

    def test_search_respects_project_ignore_files(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".gitignore").write_text("ignored/\n*.log\n", encoding="utf-8")
            (root / "visible.txt").write_text("needle visible\n", encoding="utf-8")
            (root / "debug.log").write_text("needle log\n", encoding="utf-8")
            ignored = root / "ignored"
            ignored.mkdir()
            (ignored / "hidden.txt").write_text("needle hidden\n", encoding="utf-8")
            harness = AgentHarness(AgentConfig(cwd=root, permission_mode="plan"))

            observation = harness.execute_tool(
                ToolCall(name=Tools.SEARCH, payload=json.dumps({"pattern": "needle", "path": "."}))
            )

        self.assertIn("visible.txt:1: needle visible", observation.result.output)
        self.assertNotIn("debug.log", observation.result.output)
        self.assertNotIn("hidden", observation.result.output)

    def test_search_respects_include_extensions(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.py").write_text("needle python\n", encoding="utf-8")
            (root / "a.md").write_text("needle markdown\n", encoding="utf-8")
            harness = AgentHarness(AgentConfig(cwd=root, permission_mode="plan"))

            observation = harness.execute_tool(
                ToolCall(
                    name=Tools.SEARCH,
                    payload=json.dumps({"pattern": "needle", "path": ".", "include_extensions": [".py"]}),
                )
            )

        self.assertIn("a.py:1: needle python", observation.result.output)
        self.assertNotIn("markdown", observation.result.output)

    def test_search_reports_timeout(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "large.txt").write_text("\n".join("miss" for _ in range(20000)), encoding="utf-8")
            harness = AgentHarness(AgentConfig(cwd=root, permission_mode="plan"))

            observation = harness.execute_tool(
                ToolCall(
                    name=Tools.SEARCH,
                    payload=json.dumps({"pattern": "needle", "path": ".", "timeout_ms": 1}),
                )
            )

        self.assertIn("search timed out", observation.result.output)

    def test_write_file_rejects_invalid_json_without_writing(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            harness = AgentHarness(AgentConfig(cwd=Path(tmp), permission_mode="autoEdit"))

            observation = harness.execute_tool(
                ToolCall(name=Tools.WRITE_FILE, payload=json.dumps({"path": "config.json", "content": '{"bad":'}))
            )

            self.assertFalse(path.exists())

        self.assertTrue(observation.result.skipped)
        self.assertEqual(observation.result.exit_code, 2)
        self.assertIn("Structured file validation failed", observation.result.output)
        self.assertIn("Formatting suggestion:", observation.result.output)

    def test_write_file_accepts_valid_json(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            harness = AgentHarness(AgentConfig(cwd=Path(tmp), permission_mode="autoEdit"))

            observation = harness.execute_tool(
                ToolCall(name=Tools.WRITE_FILE, payload=json.dumps({"path": "config.json", "content": '{"ok": true}'}))
            )

            self.assertEqual(path.read_text(encoding="utf-8"), '{"ok": true}')
            self.assertEqual(observation.result.exit_code, 0)
            self.assertEqual(observation.result.metadata["write_lock"], "cross_process")
            self.assertTrue(list((Path(tmp) / ".agent" / "locks").glob("*.lock")))

    def test_write_file_rejects_json_that_violates_sidecar_schema(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config.schema.json").write_text(
                json.dumps(
                    {
                        "type": "object",
                        "required": ["name"],
                        "properties": {"name": {"type": "string", "pattern": "^[a-z]+$"}},
                    }
                ),
                encoding="utf-8",
            )
            harness = AgentHarness(AgentConfig(cwd=root, permission_mode="autoEdit"))

            observation = harness.execute_tool(
                ToolCall(name=Tools.WRITE_FILE, payload=json.dumps({"path": "config.json", "content": '{"name":"ABC"}'}))
            )

            self.assertFalse((root / "config.json").exists())

        self.assertTrue(observation.result.skipped)
        self.assertEqual(observation.result.exit_code, 2)
        self.assertIn("schema validation failed", observation.result.output)
        self.assertIn("name: must match pattern", observation.result.output)
        self.assertIn("Formatting suggestion:", observation.result.output)

    def test_write_file_rejects_yaml_that_violates_sidecar_schema(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config.schema.json").write_text(
                json.dumps(
                    {
                        "type": "object",
                        "required": ["name", "tags"],
                        "properties": {
                            "name": {"type": "string", "pattern": "^[a-z]+$"},
                            "tags": {"type": "array", "items": {"type": "string"}},
                        },
                    }
                ),
                encoding="utf-8",
            )
            harness = AgentHarness(AgentConfig(cwd=root, permission_mode="autoEdit"))

            observation = harness.execute_tool(
                ToolCall(
                    name=Tools.WRITE_FILE,
                    payload=json.dumps({"path": "config.yaml", "content": "name: ABC\ntags:\n  - ok\n"}),
                )
            )

            self.assertFalse((root / "config.yaml").exists())

        self.assertTrue(observation.result.skipped)
        self.assertEqual(observation.result.exit_code, 2)
        self.assertIn("schema validation failed", observation.result.output)
        self.assertIn("name: must match pattern", observation.result.output)
        self.assertIn("Formatting suggestion:", observation.result.output)

    def test_edit_file_replaces_line_range(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "notes.txt"
            path.write_text("one\ntwo\nthree\n", encoding="utf-8")
            harness = AgentHarness(AgentConfig(cwd=root, permission_mode="autoEdit"))

            observation = harness.execute_tool(
                ToolCall(name=Tools.EDIT_FILE, payload=json.dumps({"path": "notes.txt", "start_line": 2, "end_line": 2, "content": "TWO"}))
            )

            self.assertEqual(path.read_text(encoding="utf-8"), "one\nTWO\nthree\n")

        self.assertEqual(observation.result.exit_code, 0)
        self.assertIn("Edited notes.txt lines 2-2", observation.result.output)
        self.assertEqual(observation.result.metadata["write_lock"], "cross_process")

    def test_edit_file_rejects_invalid_json_without_writing(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "config.json"
            path.write_text('{"ok": true}\n', encoding="utf-8")
            harness = AgentHarness(AgentConfig(cwd=root, permission_mode="autoEdit"))

            observation = harness.execute_tool(
                ToolCall(name=Tools.EDIT_FILE, payload=json.dumps({"path": "config.json", "start_line": 1, "end_line": 1, "content": '{"bad":'}))
            )

            self.assertEqual(path.read_text(encoding="utf-8"), '{"ok": true}\n')

        self.assertTrue(observation.result.skipped)
        self.assertEqual(observation.result.exit_code, 2)
        self.assertIn("Structured file validation failed", observation.result.output)

    def test_write_file_rejects_invalid_toml_without_writing(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "pyproject.toml"
            harness = AgentHarness(AgentConfig(cwd=Path(tmp), permission_mode="autoEdit"))

            observation = harness.execute_tool(
                ToolCall(name=Tools.WRITE_FILE, payload=json.dumps({"path": "pyproject.toml", "content": "[project\n"}))
            )

            self.assertFalse(path.exists())

        self.assertTrue(observation.result.skipped)
        self.assertIn("Structured file validation failed", observation.result.output)

    def test_write_file_rejects_invalid_xml_without_writing(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.xml"
            harness = AgentHarness(AgentConfig(cwd=Path(tmp), permission_mode="autoEdit"))

            observation = harness.execute_tool(
                ToolCall(name=Tools.WRITE_FILE, payload=json.dumps({"path": "config.xml", "content": "<root>"}))
            )

            self.assertFalse(path.exists())

        self.assertTrue(observation.result.skipped)
        self.assertIn("Structured file validation failed", observation.result.output)


if __name__ == "__main__":
    unittest.main()
