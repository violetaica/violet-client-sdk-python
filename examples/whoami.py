"""Diagnose auth/provisioning state — useful when inference returns 403.

    python -m examples.whoami
"""
from violet import APIError, Violet


def main() -> None:
    client = Violet()
    try:
        me = client.auth.login()  # POST /api/auth/login — no workspace required
    except APIError as e:
        if e.status_code == 403 and e.code == "user_not_provisioned":
            print("status: signed in to Auth0, but NOT provisioned in Violet.")
            print("fix:    client.auth.signup() to provision the account row.")
        else:
            print(f"login failed: {e.status_code} {e.message}")
        return

    ws = me.get("default_workspace") or me.get("defaultWorkspace")
    count = me.get("memberships_count", me.get("membershipsCount"))
    print(f"sub:               {me.get('sub')}")
    print(f"default_workspace: {ws}")
    print(f"memberships_count: {count}")
    print("\nReady." if ws else
          "\nNo workspace yet — inference will 403 until you have a paid workspace.")


if __name__ == "__main__":
    main()
