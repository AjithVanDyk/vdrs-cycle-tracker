"""Acumatica OData live pull module for vdrs-cycle-tracker.

Supports GitHub Actions via environment variables (ACUMATICA_USER, ACUMATICA_PASSWORD)
and local Windows execution via Windows Credential Manager ('AcumaticaOData').
"""
import os
import sys
import pandas as pd
import requests

BASE = "https://vdrs.acumatica.com"
TENANT = "VDRS"
CRED_NAME = "AcumaticaOData"
TIMEOUT = 300


def get_credentials():
    """Retrieve Acumatica username and password from env vars or Windows Credential Manager."""
    env_user = os.environ.get("ACUMATICA_USER")
    env_pw = os.environ.get("ACUMATICA_PASSWORD")
    if env_user and env_pw:
        user = env_user.strip()
        if "\\" in user:
            user = user.split("\\", 1)[1]
        if "@" in user and not user.endswith(f"@{TENANT}"):
            u_part = user.split("@", 1)[0]
            if u_part.lower() in ("asrikanth", "srikantha"):
                user = f"SrikanthA@{TENANT}"
            else:
                user = f"{u_part}@{TENANT}"
        elif "@" not in user:
            user = f"{user}@{TENANT}"
        return user, env_pw.strip()

    if os.name == "nt":
        try:
            import ctypes
            import ctypes.wintypes as wt

            class _CREDENTIAL(ctypes.Structure):
                _fields_ = [
                    ("Flags", wt.DWORD),
                    ("Type", wt.DWORD),
                    ("TargetName", wt.LPWSTR),
                    ("Comment", wt.LPWSTR),
                    ("LastWritten", wt.FILETIME),
                    ("CredentialBlobSize", wt.DWORD),
                    ("CredentialBlob", ctypes.POINTER(ctypes.c_byte)),
                    ("Persist", wt.DWORD),
                    ("AttributeCount", wt.DWORD),
                    ("Attributes", ctypes.c_void_p),
                    ("TargetAlias", wt.LPWSTR),
                    ("UserName", wt.LPWSTR),
                ]

            advapi = ctypes.windll.advapi32
            cred_ptr = ctypes.POINTER(_CREDENTIAL)()
            if advapi.CredReadW(CRED_NAME, 1, 0, ctypes.byref(cred_ptr)):
                cred = cred_ptr.contents
                password = ctypes.string_at(
                    cred.CredentialBlob, cred.CredentialBlobSize
                ).decode("utf-16-le")
                username = cred.UserName
                advapi.CredFree(cred_ptr)
                if "\\" in username:
                    username = username.split("\\", 1)[1]
                if "@" not in username:
                    username = f"{username}@{TENANT}"
                return username, password
        except Exception as e:
            print(f"Windows credential lookup warning: {e}")

    raise RuntimeError(
        "No Acumatica credentials found. Please set ACUMATICA_USER and ACUMATICA_PASSWORD "
        "environment variables (or GitHub Secrets)."
    )


def pull_gi(gi_name, top=None, select=None, filter_expr=None):
    """Pull Generic Inquiry via OData endpoint."""
    user, pw = get_credentials()
    url = f"{BASE}/odata/{TENANT}/{gi_name}"
    params = {"$format": "json"}
    if top:
        params["$top"] = int(top)
    if select:
        params["$select"] = select
    if filter_expr:
        params["$filter"] = filter_expr

    headers = {"Accept": "application/json"}
    rows = []
    page = 1
    session = requests.Session()
    session.auth = (user, pw)

    while url:
        print(f"Pulling {gi_name} page {page}...", end="\r", flush=True)
        r = session.get(url, params=params, headers=headers, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        chunk = data.get("value", [])
        rows.extend(chunk)
        url = data.get("odata.nextLink") or data.get("@odata.nextLink")
        params = None  # nextLink contains full query params
        page += 1

    print(f"Pulled {len(rows)} records from {gi_name} in {page-1} page(s).")
    df = pd.DataFrame(rows)
    return df
