
"""
The Blueprint's DNA & The Imperial Ledger: An Absolute, Secure System.

This final version integrates ACL (Access Control List) enforcement directly into the DNA,
reading from the `manifest.json` to ensure every action is authorized. It is the fulfillment
of the imperial constitution.
"""

import subprocess
import os
import sys
import json
import psutil
from typing import Dict, Any, Optional, List

# --- Constants ---
BLUEPRINT_NAME = "lunara-bot"
BLUEPRINT_ROOT = os.path.join("blueprint", "lunessasignels", "lunara-bot")
LEDGER_PATH = os.path.join(BLUEPRINT_ROOT, ".deployment_state.json")
MANIFEST_PATH = os.path.join(BLUEPRINT_ROOT, "..", "manifest.json") # manifest is one level up
SCRIPT_PATH = os.path.join(BLUEPRINT_ROOT, "run.py")

# --- Security & Ledger Functions ---

def _get_permissions() -> Dict[str, List[str]]:
    """Loads the permissions from the legion's manifest."""
    try:
        with open(MANIFEST_PATH, 'r') as f:
            manifest_data = json.load(f)
            return manifest_data.get("permissions", {})
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"CRITICAL: Could not load or parse manifest at {MANIFEST_PATH}. Defaulting to deny all. Reason: {e}")
        return {}

def _is_authorized(action: str, user_role: str) -> bool:
    """Checks if a user_role is authorized to perform an action."""
    permissions = _get_permissions()
    allowed_roles = permissions.get(action)
    if allowed_roles is None:
        print(f"SECURITY_WARNING: Action '{action}' not defined in manifest. Denying access.")
        return False
    return user_role in allowed_roles

def _read_ledger() -> Optional[Dict[str, Any]]:
    if not os.path.exists(LEDGER_PATH): return None
    try:
        with open(LEDGER_PATH, 'r') as f: return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError): return None

def _write_ledger(state: Dict[str, Any]):
    with open(LEDGER_PATH, 'w') as f: json.dump(state, f, indent=4)

def _is_pid_running(pid: int) -> bool:
    return psutil.pid_exists(pid)

# --- Secured Genes (Interface Functions) ---

def start_bot_and_narrate(user_role: str) -> Dict[str, Any]:
    """The Imperial Decree: Transcribe DNA, but only if authorized by the manifest."""
    if not _is_authorized("trigger_autotrade", user_role):
        print(f"SECURITY_VIOLATION: Role '{user_role}' attempted to trigger autotrade. Access Denied.")
        return {"narration": "অনুমোদন প্রত্যাখ্যান করা হয়েছে। আপনার এই সৈন্যদল সক্রিয় করার অনুমতি নেই।", "process": None}
    
    ledger_state = _read_ledger()
    if ledger_state and ledger_state.get("status") == "running":
        pid = ledger_state.get("pid")
        if pid and _is_pid_running(pid):
            return {"narration": f"সৈন্যদল পিআইডি {pid} দিয়ে ইতিমধ্যে সক্রিয় আছে।", "process": None}
        else:
            _write_ledger({"status": "crashed", "pid": pid, "reason": "Detected orphaned record."})

    print(f"Executing Imperial Decree (Authorized by: {user_role}): Awakening {BLUEPRINT_NAME}...")
    try:
        process = subprocess.Popen([sys.executable, SCRIPT_PATH])
        _write_ledger({"status": "running", "pid": process.pid})
        narration = f"আদেশ অনুমোদিত। লুনার-বট সৈন্যদল এখন জীবন্ত, প্রক্রিয়া আইডি {process.pid}।"
        return {"narration": narration, "process": process}
    except Exception as e:
        return {"narration": f"একটি গুরুতর ত্রুটির কারণে সৈন্যদল মোতায়েন করা যায়নি: {e}", "process": None}

def get_blueprint_status(user_role: str) -> Dict[str, Any]:
    """Reads the Ledger, but only if authorized by the manifest."""
    if not _is_authorized("view_status", user_role):
        print(f"SECURITY_VIOLATION: Role '{user_role}' attempted to view status. Access Denied.")
        return {"status": "permission_denied", "message": "You do not have permission to view the legion's status."}

    ledger_state = _read_ledger()
    if not ledger_state: return {"status": "dormant"}

    if ledger_state.get("status") == "running":
        pid = ledger_state.get("pid")
        if not (pid and _is_pid_running(pid)):
            new_state = {**ledger_state, "status": "crashed"}
            _write_ledger(new_state)
            return new_state
            
    return ledger_state

def stop_bot(user_role: str) -> Dict[str, str]:
    """Issues the Apoptosis order, but only if authorized by the manifest."""
    # Re-using 'trigger_autotrade' as stopping is as sensitive as starting.
    if not _is_authorized("trigger_autotrade", user_role):
        print(f"SECURITY_VIOLATION: Role '{user_role}' attempted to stop the bot. Access Denied.")
        return {"status": "permission_denied", "message": "You do not have permission to stop this legion."}

    ledger_state = _read_ledger()
    if not ledger_state or ledger_state.get("status") != "running":
        return {"status": "dormant"}

    pid = ledger_state.get("pid")
    if not pid or not _is_pid_running(pid):
        _write_ledger({"status": "stopped", "pid": pid})
        return {"status": "already_stopped"}

    try:
        p = psutil.Process(pid)
        p.terminate()
        p.wait(timeout=10)
        message = "The legion has been gracefully terminated."
    except psutil.TimeoutExpired:
        p.kill()
        message = "The legion was forcibly terminated."
    except psutil.NoSuchProcess:
        message = "The legion vanished before the decree could be delivered."
    finally:
        _write_ledger({"status": "stopped", "pid": pid})

    return {"status": "terminated", "message": message}
