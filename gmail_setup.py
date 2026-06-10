"""
gmail_setup.py — One-time OAuth2 setup for Gmail API access.

Run this ONCE locally on your machine. It will open a browser window,
ask you to authorise access to your Gmail, and save a token file
(gmail_token.json) that the agent uses on every subsequent run.

Steps before running this script:
  1. Go to https://console.cloud.google.com
  2. Create a new project (or use existing)
  3. Enable the Gmail API
  4. Create OAuth 2.0 credentials → Desktop App
  5. Download the credentials JSON → save as gmail_credentials.json in this directory
  6. Run: python gmail_setup.py

The token is valid until revoked. In GitHub Actions, base64-encode the
token file and store it as a secret (GMAIL_TOKEN_B64). The workflow
decodes it before running.
"""

import json
import os
from pathlib import Path

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",  # read emails for job extraction
]

CREDENTIALS_FILE = "gmail_credentials.json"
TOKEN_FILE = "gmail_token.json"


def main():
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
    except ImportError:
        print("Missing dependencies. Run: pip install google-auth-oauthlib google-auth-httplib2 google-api-python-client")
        return

    if not Path(CREDENTIALS_FILE).exists():
        print(f"\n❌  {CREDENTIALS_FILE} not found.")
        print("    Download it from Google Cloud Console → APIs & Services → Credentials")
        print("    Save it as gmail_credentials.json in this directory, then re-run.\n")
        return

    creds = None

    # Load existing token if present
    if Path(TOKEN_FILE).exists():
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    # Refresh or re-authenticate
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            from google.auth.transport.requests import Request
            creds.refresh(Request())
            print("✅  Token refreshed.")
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
            print("✅  Authorisation complete.")

        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())

    print(f"\n✅  Token saved to {TOKEN_FILE}")
    print("\nNext steps:")
    print("  Local runs  → the agent reads gmail_token.json automatically.")
    print("  GitHub CI   → run the command below and add the output as secret GMAIL_TOKEN_B64:")
    print(f"\n    base64 -i {TOKEN_FILE} | tr -d '\\n'  (macOS/Linux)")
    print(f"    [Convert]::ToBase64String([IO.File]::ReadAllBytes('{TOKEN_FILE}'))  (PowerShell)\n")


if __name__ == "__main__":
    main()
