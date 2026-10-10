import argparse
import getpass
import re
import sys
from .auth import new_hash

def valid_password(password, strong_only=False):
    if strong_only:
        return len(password) >= 12
    return bool(re.fullmatch(r"[0-9]{4}", password)) or len(password) >= 12

def main():
    parser=argparse.ArgumentParser(description="DarkWatch administrator tools")
    parser.add_argument('command', choices=['hash-password'])
    parser.add_argument('--strong-only', action='store_true')
    parser.add_argument('--with-type', action='store_true')
    args=parser.parse_args()
    if args.command=='hash-password':
        policy='12+ characters' if args.strong_only else '4-digit numeric PIN or 12+ characters'
        pw=getpass.getpass('Admin password ('+policy+'): ')
        repeat=getpass.getpass('Repeat: ')
        if pw != repeat or not valid_password(pw, args.strong_only):
            sys.exit('Password mismatch or invalid length ('+policy+').')
        if len(pw)==4:
            print('WARNING: A four-digit PIN is for private SSH tunnel access only.',file=sys.stderr)
        kind='pin' if bool(re.fullmatch(r'[0-9]{4}',pw)) else 'strong'
        digest=new_hash(pw)
        print((kind+'|'+digest) if args.with_type else digest)
if __name__=='__main__':
    main()
