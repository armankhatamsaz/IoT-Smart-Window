"""Manage the catalog while the system is running, through its REST API.

    python tools/catalog_admin.py users
    python tools/catalog_admin.py devices
    python tools/catalog_admin.py services
    python tools/catalog_admin.py add-user user3 1234 "Living Room" "Bedroom 2"
    python tools/catalog_admin.py set-zones 3 "Bedroom 1"
    python tools/catalog_admin.py remove-user 3

Nothing is restarted and no source code changes: the dashboard and the bot
read users and zones from the catalog at login.
"""
import sys
import argparse

import requests


class CatalogAdmin:
    def __init__(self, registry_url):
        self.url = registry_url.rstrip("/")

    def get(self, collection):
        resp = requests.get(f"{self.url}/{collection}", timeout=5)
        resp.raise_for_status()
        return resp.json()

    def known_zones(self):
        # The zones are the ones the running gateways registered, so a new
        # room only needs a new gateway, not a code change.
        return sorted({d["zone"] for d in self.get("devicesList") if d.get("zone")})

    def check_zones(self, zones):
        known = self.known_zones()
        unknown = [z for z in zones if z not in known]
        if unknown:
            raise ValueError(f"unknown zone(s) {unknown}, the registered zones are {known}")

    def add_user(self, name, password, zones):
        users = self.get("usersList")
        if any(u["userName"] == name for u in users):
            raise ValueError(f"user '{name}' already exists")
        self.check_zones(zones)
        user_id = max((u["userID"] for u in users), default=0) + 1
        user = {"userID": user_id, "userName": name, "password": password, "allowed_zones": zones}
        requests.post(f"{self.url}/usersList", json=user, timeout=5).raise_for_status()
        return user_id

    def set_zones(self, user_id, zones):
        self.check_zones(zones)
        resp = requests.put(f"{self.url}/usersList", json={"id": user_id, "allowed_zones": zones}, timeout=5)
        resp.raise_for_status()

    def remove_user(self, user_id):
        requests.delete(f"{self.url}/usersList/{user_id}", timeout=5).raise_for_status()


def print_users(admin):
    for u in admin.get("usersList"):
        linked = "Telegram linked" if u.get("chatID") else ""
        print(f"{u['userID']:>3}  {u['userName']:<10} {', '.join(u['allowed_zones']):<30} {linked}")


def print_devices(admin):
    for d in admin.get("devicesList"):
        rest = next((s.get("serviceIP") for s in d.get("servicesDetails", []) if s.get("serviceType") == "REST"), "")
        print(f"{d['deviceID']:<22} {d.get('zone', ''):<12} {', '.join(d['availableServices']):<10} "
              f"{rest:<26} last seen {d.get('lastUpdate', '')}")


def print_services(admin):
    for s in admin.get("servicesList"):
        endpoints = [e for d in s.get("servicesDetails", []) for e in d.get("endpoints", [])]
        print(f"{s['serviceName']:<36} {', '.join(s['availableServices']):<10} {' '.join(endpoints)}")


def main():
    parser = argparse.ArgumentParser(description="Manage the Smart Window catalog at runtime.")
    parser.add_argument("--registry", default="http://localhost:8080", help="catalog address")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("users", help="list users and their zones")
    commands.add_parser("devices", help="list registered devices")
    commands.add_parser("services", help="list registered services")
    cmd = commands.add_parser("add-user", help="add a user allowed in some zones")
    cmd.add_argument("name")
    cmd.add_argument("password")
    cmd.add_argument("zones", nargs="+")
    cmd = commands.add_parser("set-zones", help="change the zones of a user")
    cmd.add_argument("user_id", type=int)
    cmd.add_argument("zones", nargs="+")
    cmd = commands.add_parser("remove-user", help="remove a user")
    cmd.add_argument("user_id", type=int)
    args = parser.parse_args()

    admin = CatalogAdmin(args.registry)
    try:
        if args.command == "users":
            print_users(admin)
        elif args.command == "devices":
            print_devices(admin)
        elif args.command == "services":
            print_services(admin)
        elif args.command == "add-user":
            user_id = admin.add_user(args.name, args.password, args.zones)
            print(f"User '{args.name}' added with ID {user_id}.")
        elif args.command == "set-zones":
            admin.set_zones(args.user_id, args.zones)
            print(f"User {args.user_id} can now use: {', '.join(args.zones)}")
        elif args.command == "remove-user":
            admin.remove_user(args.user_id)
            print(f"User {args.user_id} removed.")
    except (requests.RequestException, ValueError) as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
