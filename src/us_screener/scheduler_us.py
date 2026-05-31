"""US-specific launchd schedule wrapper."""

from __future__ import annotations

import shlex
import sys
from pathlib import Path


LABEL = "com.us-screener.premarket"


def _resolve_python(repo_dir: Path) -> str:
    """Pick a Python that can import us_screener for the launchd job.

    Prefer the project's ``.venv`` interpreter if present (project convention),
    otherwise fall back to the interpreter running this install. Invoking
    ``python -m us_screener.cli`` avoids depending on where the console script
    landed (it may be in a base env's bin rather than ``.venv/bin``).
    """
    venv_python = repo_dir / ".venv" / "bin" / "python"
    if venv_python.exists():
        return str(venv_python)
    return sys.executable


def install_us_launchd_schedule(
    repo_dir: Path,
    hour: int,
    minute: int,
    history_top: int = 4000,
    lookback_days: int = 430,
    fundamentals_top: int = 0,
    label: str = LABEL,
) -> tuple[Path, Path]:
    repo_dir = repo_dir.resolve()
    python_bin = shlex.quote(_resolve_python(repo_dir))
    script_dir = repo_dir / "scripts"
    log_dir = repo_dir / "logs"
    launch_agents = Path.home() / "Library" / "LaunchAgents"
    script_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    launch_agents.mkdir(parents=True, exist_ok=True)

    script_path = script_dir / "us_premarket_update.sh"
    plist_path = launch_agents / f"{label}.plist"
    repo_shell = shlex.quote(str(repo_dir))
    script_path.write_text(
        "\n".join(
            [
                "#!/bin/zsh",
                "set -euo pipefail",
                f"cd {repo_shell}",
                'LOCK_DIR=".us-update.lock"',
                'if ! mkdir "$LOCK_DIR" 2>/dev/null; then',
                '  echo "$(date +%Y-%m-%dT%H:%M:%S%z) us update skipped: another run is active"',
                "  exit 0",
                "fi",
                'trap \'rmdir "$LOCK_DIR"\' EXIT INT TERM',
                (
                    f"{python_bin} -m us_screener.cli update "
                    f"--history-top {history_top} "
                    f"--lookback-days {lookback_days} "
                    f"--fundamentals-top {fundamentals_top} --json"
                ),
                "",
            ]
        ),
        encoding="utf-8",
    )
    script_path.chmod(0o755)
    plist_path.write_text(
        f"""<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\"
  \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">
<plist version=\"1.0\">
<dict>
  <key>Label</key>
  <string>{label}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/zsh</string>
    <string>{script_path}</string>
  </array>
  <key>WorkingDirectory</key>
  <string>{repo_dir}</string>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>{hour}</integer>
    <key>Minute</key>
    <integer>{minute}</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>{log_dir / "us-premarket.out.log"}</string>
  <key>StandardErrorPath</key>
  <string>{log_dir / "us-premarket.err.log"}</string>
  <key>RunAtLoad</key>
  <false/>
</dict>
</plist>
""",
        encoding="utf-8",
    )
    return script_path, plist_path
