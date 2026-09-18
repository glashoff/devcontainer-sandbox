#!/usr/bin/env python3
"""Reports the result of the daily image build (ExecStopPost= of
devcontainer-build-image.service).

Always shows a desktop notification, and sends a mail when the build failed,
over SMTP directly. The account is configured outside this repository, in
~/.config/devcontainer-sandbox/config (see config.example); without those
settings only the notification appears.

Failures of this script must never fail the build, which is why the unit calls
it with a leading "-".
"""

import os
import smtplib
import socket
import ssl
import subprocess
import sys
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import CONFIG_FILE, read_config  # noqa: E402

SERVICE = "devcontainer-build-image.service"
JOURNAL_LINES = "40"


def notify(summary: str, body: str, timeout: int) -> None:
    """Desktop notification via gdbus, which GNOME brings along (no libnotify).

    timeout 0 means it stays until it is dismissed.
    """
    subprocess.run(
        ["gdbus", "call", "--session",
         "--dest", "org.freedesktop.Notifications",
         "--object-path", "/org/freedesktop/Notifications",
         "--method", "org.freedesktop.Notifications.Notify",
         "devcontainer-sandbox", "0", "", summary, body, "[]", "{}", str(timeout)],
        capture_output=True)


def journal() -> str:
    """The last lines the build wrote, as the body of the mail."""
    result = subprocess.run(
        ["journalctl", "--user", "-u", SERVICE, "-n", JOURNAL_LINES,
         "--no-pager", "--output", "cat"],
        capture_output=True, text=True)
    return result.stdout or "(no journal output)"


def send_mail() -> None:
    config = read_config()
    missing = [key for key in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "MAIL_TO")
               if not config.get(key)]
    if missing:
        print(f"No mail sent: {CONFIG_FILE} missing or without "
              f"{', '.join(missing)}", file=sys.stderr)
        return

    host = socket.gethostname()
    message = EmailMessage()
    message["Subject"] = f"Sandbox image build failed on {host}"
    message["From"] = config.get("MAIL_FROM") or config["SMTP_USER"]
    message["To"] = config["MAIL_TO"]
    message.set_content(
        f"The daily build of local/devcontainer-sandbox failed on {host} "
        f"at {datetime.now():%Y-%m-%d %H:%M}.\n\n"
        f"Last {JOURNAL_LINES} journal lines:\n\n{journal()}\n"
        f"Full log: journalctl --user -u {SERVICE}\n")
    try:
        with smtplib.SMTP_SSL(config["SMTP_HOST"],
                              int(config.get("SMTP_PORT") or 465),
                              context=ssl.create_default_context(),
                              timeout=60) as smtp:
            smtp.login(config["SMTP_USER"], config["SMTP_PASSWORD"])
            smtp.send_message(message)
    except (OSError, smtplib.SMTPException) as error:
        # The notification is already out; a build must not fail over a mail.
        print(f"Cannot send mail: {error}", file=sys.stderr)


def main() -> None:
    # systemd sets SERVICE_RESULT for ExecStopPost.
    if os.environ.get("SERVICE_RESULT") == "success":
        notify("Sandbox image updated", "local/devcontainer-sandbox:latest", 8000)
        return
    notify("Sandbox image build failed", f"journalctl --user -u {SERVICE}", 0)
    send_mail()


if __name__ == "__main__":
    main()
