# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import argparse
from getpass import getpass

from app.db import Store
from app.settings import Settings

parser = argparse.ArgumentParser()
parser.add_argument("command", choices=["create-user", "migrate"])
parser.add_argument("--username")
args = parser.parse_args()
store = Store(Settings().data_dir)
if args.command == "create-user":
    name = args.username or input("Username: ").strip()
    password = getpass("Password (at least 12 characters): ")
    if password != getpass("Repeat password: "):
        raise SystemExit("Passwords do not match")
    store.create_user(name, password, True)
    print("User created")
else:
    print("Database initialized")
