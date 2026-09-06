from __future__ import annotations

from pathlib import Path
import shlex
import sys
import tempfile
import unittest

from irag import config, providers


class ProviderTests(unittest.TestCase):
    def test_custom_provider_normalizes_stdin_and_output(self):
        with tempfile.TemporaryDirectory() as raw:
            script = Path(raw) / "provider.py"
            script.write_text(
                "import sys\nprint('answer:' + sys.stdin.read())\n",
                encoding="utf-8")
            command = f"{shlex.quote(sys.executable)} {shlex.quote(str(script))}"
            cfg = {"llm": {"provider": "custom", "command": command,
                           "model_label": "unit", "timeout": 10,
                           "retries": 0}}
            result = providers.run(cfg, "hello", purpose="test")
            self.assertEqual(result.text, "answer:hello")
            self.assertEqual(result.provider, "custom")
            self.assertEqual(result.attempts, 1)

    def test_builtin_invocations_do_not_depend_on_claude(self):
        codex = providers.invocation(
            {"llm": {"provider": "codex", "command": "claude -p",
                     "model": "gpt-test"}}, "prompt")
        self.assertEqual(codex.argv[0:2], ["codex", "exec"])
        self.assertIn("gpt-test", codex.argv)
        self.assertEqual(providers.configured_model(
            {"llm": {"provider": "codex", "model": "",
                     "model_label": "claude"}}), "")
        self.assertEqual(providers.model_label(
            {"llm": {"provider": "codex", "model": "",
                     "model_label": "claude"}}), "codex")
        ollama = providers.invocation(
            {"llm": {"provider": "ollama", "model": "qwen3"}}, "prompt")
        self.assertEqual(ollama.argv, ["ollama", "run", "qwen3"])
        self.assertEqual(providers.configured_model(
            {"llm": {"provider": "ollama", "model": "qwen3"}}), "qwen3")

    def test_agy_puts_print_last_so_it_cannot_eat_another_flag(self):
        # agy's --print consumes the next argv token as its prompt. With
        # --print first, agy took "--output-format" as the prompt and ignored
        # the real one, so every synthesis failed with exit 2.
        agy = providers.invocation(
            {"llm": {"provider": "agy", "model": ""}}, "PROMPT")
        self.assertEqual(agy.prompt_mode, "argv")
        self.assertEqual(agy.argv[-2:], ["--print", "PROMPT"])
        self.assertNotIn("--print", agy.argv[:-2])
        # a configured model must also stay ahead of --print
        with_model = providers.invocation(
            {"llm": {"provider": "agy", "model": "gemini-test"}}, "PROMPT")
        self.assertEqual(with_model.argv[-2:], ["--print", "PROMPT"])
        self.assertIn("gemini-test", with_model.argv[:-2])

    def test_only_custom_provider_requires_a_command(self):
        builtin = config.DEFAULTS.copy()
        builtin["llm"] = dict(config.DEFAULTS["llm"], provider="codex",
                              command="")
        self.assertEqual(config.validation_errors(builtin), [])
        custom = config.DEFAULTS.copy()
        custom["llm"] = dict(config.DEFAULTS["llm"], provider="custom",
                             command="")
        self.assertIn("[llm].command must not be empty for provider=custom",
                      config.validation_errors(custom))

    def test_promptfile_receives_no_duplicate_stdin_and_probe_cleans_up(self):
        with tempfile.TemporaryDirectory() as raw:
            script = Path(raw) / "provider.py"
            script.write_text(
                "import pathlib,sys\n"
                "print(pathlib.Path(sys.argv[1]).read_text() + '|' + "
                "sys.stdin.read())\n", encoding="utf-8")
            command = (f"{shlex.quote(sys.executable)} "
                       f"{shlex.quote(str(script))} {{promptfile}}")
            cfg = {"llm": {"provider": "custom", "command": command,
                           "model_label": "unit", "timeout": 10,
                           "retries": 0}}
            result = providers.run(cfg, "once", purpose="test")
            self.assertEqual(result.text, "once|")
            before = set(Path(tempfile.gettempdir()).glob("tmp*.txt"))
            ready, _ = providers.availability(cfg)
            after = set(Path(tempfile.gettempdir()).glob("tmp*.txt"))
            self.assertTrue(ready)
            self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
