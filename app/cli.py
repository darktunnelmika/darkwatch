import argparse
import getpass
import os
import sys
from .auth import new_hash


def main():
    parser = argparse.ArgumentParser(description='DarkWatch administrator tools')
    parser.add_argument('command', choices=['hash-password'])
    args = parser.parse_args()
    if args.command == 'hash-password':
        pw = getpass.getpass('New password (minimum 12 characters): ')
        confirm = getpass.getpass('Repeat: ')
        if pw != confirm or len(pw) < 12:
            sys.exit('Password mismatch or too short (min 12 chars).')
        print(new_hash(pw))
if __name__ == '__main__': main()
