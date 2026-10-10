import argparse
import getpass
import re
import sys

from .auth import new_hash


def valid_password(password):
    """Allow a local-only four-digit PIN or a conventional longer password."""
    return bool(re.fullmatch(r"[0-9]{4}", password)) or len(password) >= 12


def main():
    parser = argparse.ArgumentParser(description='DarkWatch administrator tools')
    parser.add_argument('command', choices=['hash-password'])
    args = parser.parse_args()
    if args.command == 'hash-password':
        pw = getpass.getpass('Admin password (4-digit PIN or 12+ characters): ')
        confirm = getpass.getpass('Repeat: ')
        if pw != confirm:
            sys.exit('Passwords do not match. Run the installer again.')
        if not valid_password(pw):
            sys.exit('Use exactly 4 numeric digits OR a password of 12+ characters.')
        if len(pw) == 4:
            print('WARNING: Four-digit PIN is weak. Use ONLY with a private SSH tunnel, never on a public dashboard.', file=sys.stderr)
        print(new_hash(pw))


if __name__ == '__main__':
    main()
