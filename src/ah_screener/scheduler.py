from __future__ import annotations

from pathlib import Path


def install_launchd_schedule(
    repo_dir: Path,
    hour: int = 18,
    minute: int = 30,
    top: int = 120,
    lookback_days: int = 430,
    label: str = "com.ah-screener.update",
) -> tuple[Path, Path]:
    repo_dir = repo_dir.resolve()
    script_dir = repo_dir / "scripts"
    log_dir = repo_dir / "logs"
    launch_agents = Path.home() / "Library" / "LaunchAgents"
    script_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    launch_agents.mkdir(parents=True, exist_ok=True)

    script_path = script_dir / "update_all.sh"
    plist_path = launch_agents / f"{label}.plist"
    script_path.write_text(
        "\n".join(
            [
                "#!/bin/zsh",
                "set -euo pipefail",
                f"cd {repo_dir}",
                (
                    ".venv/bin/ah-screener update-all "
                    f"--top {top} "
                    f"--lookback-days {lookback_days} "
                    "--industry-limit 50 "
                    "--concept-limit 120"
                ),
                "",
            ]
        ),
        encoding="utf-8",
    )
    script_path.chmod(0o755)

    plist_path.write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
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
  <string>{log_dir / "scheduled-update.out.log"}</string>
  <key>StandardErrorPath</key>
  <string>{log_dir / "scheduled-update.err.log"}</string>
  <key>RunAtLoad</key>
  <false/>
</dict>
</plist>
""",
        encoding="utf-8",
    )
    return script_path, plist_path


def uninstall_launchd_schedule(label: str = "com.ah-screener.update") -> Path:
    plist_path = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    if plist_path.exists():
        plist_path.unlink()
    return plist_path
