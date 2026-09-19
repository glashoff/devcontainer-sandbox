#!/usr/bin/env python3
"""Sets up SSH access from dev containers to one server (README "Server access").

Creates the certificate authority on this host if it does not exist yet, and
teaches the server to accept its certificates.

Each project logs in as its own user on the server (SERVER_SSH_USER in its
.devcontainer/sandbox.env), so that one project cannot touch another project's
files. This script creates those users if the server does not have them yet
and allows them to use certificates. They are named in SERVER_PRINCIPAL_USERS
in ~/.config/devcontainer-sandbox/config, which is the one place that decides
the list: the server, not a project.

The server keeps refusing certificates for every user without a file in
/etc/ssh/devcontainer_principals/, and ordinary keys in authorized_keys are not
touched. Use --dry-run to read the commands before they run.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import CA_DIR, CA_KEY, CONFIG_FILE, read_config  # noqa: E402

CA_PUBLIC_KEY = CA_KEY.with_name(CA_KEY.name + ".pub")


def create_ca() -> str:
    """The CA signs a certificate on every devcontainer-start, so it has no
    passphrase. Protect it like your own SSH key: it can create certificates
    for every user a server accepts it for."""
    if not CA_PUBLIC_KEY.is_file():
        CA_DIR.mkdir(parents=True, exist_ok=True)
        CA_DIR.chmod(0o700)
        subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "",
                        "-C", "devcontainer-sandbox-ca", "-f", str(CA_KEY)],
                       check=True, stdout=subprocess.DEVNULL)
        print(f"Certificate authority created: {CA_KEY}")
    else:
        print(f"Certificate authority: {CA_KEY}")
    return CA_PUBLIC_KEY.read_text().strip()


def remote_script(ca_public_key: str, users: list[str]) -> str:
    # Create the user if the server does not have it yet, without a password
    # (useradd leaves it locked) and without any group beyond its own, so a
    # container that logs in is an ordinary unprivileged user.
    principals = "\n".join(f"""\
if id -u {user} >/dev/null 2>&1; then
  echo "user {user} already exists"
else
  useradd --create-home --shell /bin/bash {user}
  echo "user {user} created"
fi
printf '%s\\n' {user} > /etc/ssh/devcontainer_principals/{user}""" for user in users)
    return f"""set -eu
cat > /etc/ssh/devcontainer_user_ca.pub <<'CA_KEY_EOF'
{ca_public_key}
CA_KEY_EOF
chmod 644 /etc/ssh/devcontainer_user_ca.pub

cat > /etc/ssh/sshd_config.d/20-devcontainer-ca.conf <<'SSHD_EOF'
# Accept 24-hour certificates from dev containers (devcontainer-sandbox).
TrustedUserCAKeys /etc/ssh/devcontainer_user_ca.pub
# Default deny: only users with a file here accept certificates, and only
# for the names listed in it.
AuthorizedPrincipalsFile /etc/ssh/devcontainer_principals/%u
SSHD_EOF
chmod 644 /etc/ssh/sshd_config.d/20-devcontainer-ca.conf

mkdir -p /etc/ssh/devcontainer_principals
chmod 755 /etc/ssh/devcontainer_principals
{principals}

# Refuse to reload a configuration sshd cannot parse, so nobody is locked out.
sshd -t
# A socket-activated sshd has no running service to reload; it starts a fresh
# sshd per connection and reads the new configuration by itself.
if systemctl is-active --quiet ssh.service; then
  systemctl reload ssh.service
else
  echo "sshd is socket-activated, new connections use the new configuration"
fi

echo "--- effective settings:"
sshd -T | grep -iE 'trustedusercakeys|authorizedprincipalsfile'
echo "--- users a container may log in as:"
ls -1 /etc/ssh/devcontainer_principals/ || echo "(none yet: certificates are refused)"
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="print the commands for the server instead of running them")
    args = parser.parse_args()

    config = read_config()
    ca_public_key = create_ca()

    admin = config.get("SERVER_ADMIN")
    if not admin:
        print(f"No SERVER_ADMIN in {CONFIG_FILE}, nothing to do on a server.")
        print("The certificate authority is ready; a project enables access in "
              ".devcontainer/sandbox.env.")
        return

    users = [user.strip() for user in
             config.get("SERVER_PRINCIPAL_USERS", "").split(",") if user.strip()]
    for user in users:
        if not re.fullmatch(r"[a-z_][a-z0-9_-]*", user):
            sys.exit(f"Invalid user name in SERVER_PRINCIPAL_USERS: '{user}'")
    if "root" in users:
        # Allowed, because it is the owner's server, but never quietly: this
        # takes the server out of everything the sandbox protects.
        print("\n*** root is in SERVER_PRINCIPAL_USERS ***\n"
              "A project asking for it gets a root certificate for this "
              "server. Everything in such a container - the agent and every\n"
              "npm or pip dependency it pulls - can then take the server over "
              "for good: root leaves a key of its own behind, which the\n"
              "24-hour expiry does nothing against. Grant it only to a project "
              "that provisions this server, or use a disposable one.\n",
              file=sys.stderr)
    if not users:
        print("No SERVER_PRINCIPAL_USERS: the server will trust the CA but "
              "refuse every login, because no project has a user there yet.")

    script = remote_script(ca_public_key, users)
    if args.dry_run:
        print(f"\n# Would run on {admin}:\n")
        print(script)
        return

    print(f"Configuring {admin} ...")
    result = subprocess.run(["ssh", admin, "bash -s"], input=script, text=True)
    if result.returncode != 0:
        sys.exit(f"Failed on {admin}; the server's sshd was not reloaded "
                 "unless it printed the settings above.")
    if users:
        print(f"\nUsers a project may log in as: {', '.join(users)}. "
              "Set SERVER_SSH_USER in its .devcontainer/sandbox.env "
              "accordingly. Deleting a file in /etc/ssh/devcontainer_principals/ "
              "cuts that user off at once, before its certificates expire.")


if __name__ == "__main__":
    main()
