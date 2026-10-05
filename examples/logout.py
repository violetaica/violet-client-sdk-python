"""Sign out.

    python -m examples.logout            # revoke refresh token + clear local cache
    python -m examples.logout --browser  # also end the Auth0 SSO session
"""
import sys

from violet import Violet


def main() -> None:
    browser = "--browser" in sys.argv[1:]
    client = Violet()
    client.auth.logout(browser=browser)
    print("Logged out." + (" Auth0 session cleared." if browser else ""))


if __name__ == "__main__":
    main()
