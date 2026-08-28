import subprocess
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from pc_activity_logger import scheduler


class SchedulerTests(unittest.TestCase):
    def test_source_registration_uses_gui_module(self) -> None:
        completed = Mock(spec=subprocess.CompletedProcess)
        completed.returncode = 0
        completed.stdout = ""
        completed.stderr = ""
        with patch("pc_activity_logger.scheduler.subprocess.run", return_value=completed) as run:
            scheduler.register(Path("C:/settings/config.yaml"))

        command = run.call_args.args[0]
        self.assertIn("-Gui", command)
        self.assertIn(str(Path("C:/settings/config.yaml")), command)


if __name__ == "__main__":
    unittest.main()
