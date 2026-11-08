"""Tests for stopping idle containers (host/idle-stop.py, host/activity.py).

Docker is replaced by fakes; run with: python3 -m unittest discover tests
"""

import importlib.util
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

HOST = Path(__file__).resolve().parent.parent / "host"
sys.path.insert(0, str(HOST))
import activity  # noqa: E402

spec = importlib.util.spec_from_file_location("idle_stop", HOST / "idle-stop.py")
idle_stop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(idle_stop)

MINUTE = 60


class LastClaudeActivity(unittest.TestCase):
    def test_newest_trace(self):
        with mock.patch.object(activity, "docker_value",
                               return_value="1794180648.4650528860"):
            self.assertEqual(activity.last_claude_activity("c"), 1794180648.4650528860)

    def test_no_trace(self):
        with mock.patch.object(activity, "docker_value", return_value=""):
            self.assertEqual(activity.last_claude_activity("c"), 0)

    def test_docker_failed(self):
        with mock.patch.object(activity, "docker_value", return_value=None):
            self.assertIsNone(activity.last_claude_activity("c"))

    def test_looks_in_both_places(self):
        with mock.patch.object(activity, "docker_value", return_value="") as docker:
            activity.last_claude_activity("c")
        script = docker.call_args.args[-1]
        self.assertIn("/home/*/.claude/projects", script)
        self.assertIn("/home/*/.cache/agent-relay-mcp", script)


class Run(unittest.TestCase):
    """One run of idle-stop.py over a single container."""

    def setUp(self):
        self.state = tempfile.TemporaryDirectory()
        self.idle_dir = Path(self.state.name)
        self.now = time.time()
        # What each look inside the container finds, in turn.
        self.looks = []
        self.traces = []
        self.stopped = []

    def tearDown(self):
        self.state.cleanup()

    def first_seen(self, minutes_ago):
        (self.idle_dir / "c1").write_text(f"{self.now - minutes_ago * MINUTE}\n")

    def run_idle_stop(self, *args):
        def docker_stop(command, **kwargs):
            self.stopped.append(command[-1])
            return mock.Mock(returncode=0, stderr="")

        patches = [
            mock.patch.object(idle_stop, "IDLE_DIR", self.idle_dir),
            mock.patch.object(idle_stop, "read_config", return_value={}),
            mock.patch.object(idle_stop, "sandbox_containers",
                              return_value={"c1": "/projects/p"}),
            mock.patch.object(idle_stop, "read_settings", return_value={}),
            mock.patch.object(idle_stop, "sandbox_env"),
            mock.patch.object(idle_stop, "activity",
                              side_effect=lambda _: self.looks.pop(0)),
            mock.patch.object(idle_stop, "last_claude_activity",
                              side_effect=lambda _: self.traces.pop(0)),
            mock.patch.object(idle_stop.subprocess, "run", side_effect=docker_stop),
            mock.patch.object(idle_stop.time, "time", return_value=self.now),
            mock.patch.object(sys, "argv", ["idle-stop.py", *args]),
        ]
        for patch in patches:
            patch.start()
        try:
            idle_stop.main()
        finally:
            for patch in reversed(patches):
                patch.stop()

    def test_idle_long_enough_is_stopped(self):
        self.first_seen(35)
        self.looks = ["", ""]
        self.traces = [self.now - 40 * MINUTE] * 2
        self.run_idle_stop()
        self.assertEqual(self.stopped, ["c1"])
        self.assertFalse((self.idle_dir / "c1").exists())

    def test_in_use_resets_the_idle_time(self):
        self.first_seen(35)
        self.looks = ["Claude Code"]
        self.run_idle_stop()
        self.assertEqual(self.stopped, [])
        self.assertFalse((self.idle_dir / "c1").exists())

    def test_claude_run_between_two_looks_counts(self):
        # The case that stopped a container a minute after a voice app turn:
        # no look ever saw the Claude process, but it wrote its traces.
        self.first_seen(33)
        self.looks = [""]
        self.traces = [self.now - 1 * MINUTE]
        self.run_idle_stop()
        self.assertEqual(self.stopped, [])
        self.assertTrue((self.idle_dir / "c1").exists())

    def test_first_look_records_the_time(self):
        self.looks = [""]
        self.traces = [0]
        self.run_idle_stop()
        self.assertEqual(self.stopped, [])
        self.assertEqual(float((self.idle_dir / "c1").read_text()), self.now)

    def test_claude_starting_just_before_the_stop_keeps_it(self):
        self.first_seen(35)
        self.looks = ["", "Claude Code"]
        self.traces = [self.now - 40 * MINUTE]
        self.run_idle_stop()
        self.assertEqual(self.stopped, [])

    def test_new_trace_just_before_the_stop_keeps_it(self):
        self.first_seen(35)
        self.looks = ["", ""]
        self.traces = [self.now - 40 * MINUTE, self.now]
        self.run_idle_stop()
        self.assertEqual(self.stopped, [])

    def test_unreadable_traces_are_left_alone(self):
        self.first_seen(35)
        self.looks = [""]
        self.traces = [None]
        self.run_idle_stop()
        self.assertEqual(self.stopped, [])


if __name__ == "__main__":
    unittest.main()
