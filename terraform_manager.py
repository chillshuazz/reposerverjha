import asyncio
import json
import os
from pathlib import Path
from generate_tf import get_session_dir

async def run_terraform(session_id: str) -> tuple[bool, dict]:
    workdir = get_session_dir(session_id)

    init_ok, init_out, init_err = await _run_cmd("terraform", "init", "-input=false", cwd=workdir)
    if not init_ok:
        return False, {"error": init_out + init_err, "step": "init"}

    apply_ok, apply_out, apply_err = await _run_cmd(
        "terraform", "apply", "-auto-approve", "-input=false",
        cwd=workdir, timeout=600
    )
    if not apply_ok:
        return False, {"error": apply_out + apply_err, "step": "apply"}

    ok, out_json, output_err = await _run_cmd("terraform", "output", "-json", cwd=workdir)
    if not ok:
        return False, {"error": out_json + output_err, "step": "output"}

    try:
        outputs = json.loads(out_json)
    except json.JSONDecodeError:
        return False, {"error": "Failed to parse terraform output", "step": "parse"}

    public_ips = outputs.get("ampere_a1_public_ips", {}).get("value", [])
    private_ips = outputs.get("ampere_a1_private_ips", {}).get("value", [])

    ssh_key_path = workdir / "oci-id_rsa"
    ssh_key = ssh_key_path.read_text() if ssh_key_path.exists() else ""

    return True, {
        "public_ips": public_ips,
        "private_ips": private_ips,
        "ssh_private_key": ssh_key,
        "apply_output": apply_out,
    }

DEFAULT_REGION = "sa-santiago-1"

async def _run_cmd(*args, cwd: Path, timeout: int = 300) -> tuple[bool, str, str]:
    try:
        env = os.environ.copy()
        env["OCI_REGION"] = DEFAULT_REGION
        proc = await asyncio.create_subprocess_exec(
            *args,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        out = stdout.decode("utf-8", errors="replace")
        err = stderr.decode("utf-8", errors="replace")
        return proc.returncode == 0, out, err
    except asyncio.TimeoutError:
        return False, "", f"Timeout after {timeout}s"
    except Exception as e:
        return False, "", str(e)

def is_capacity_error(output: str) -> bool:
    lower = output.lower()
    keywords = [
        "out of capacity",
        "out of host capacity",
        "insufficient capacity",
        "fault: out of host capacity",
        "cannot provision",
    ]
    return any(k in lower for k in keywords)
