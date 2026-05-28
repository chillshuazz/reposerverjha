import shutil
import uuid
from pathlib import Path

BASE_DIR = Path(__file__).parent
MODULE_DIR = BASE_DIR / "terraform_module"
SESSIONS_DIR = BASE_DIR / "terraform_sessions"

def create_session() -> str:
    session_id = str(uuid.uuid4())[:8]
    session_dir = SESSIONS_DIR / session_id
    shutil.copytree(MODULE_DIR, session_dir, dirs_exist_ok=True)
    return session_id

def write_tfvars(session_id: str, tenancy_ocid: str, user_ocid: str,
                  fingerprint: str, pem_content: str, os_image: str):
    session_dir = SESSIONS_DIR / session_id
    content = f'''tenancy_ocid     = "{tenancy_ocid}"
user_ocid        = "{user_ocid}"
fingerprint      = "{fingerprint}"
private_key = <<EOF
{pem_content}
EOF
oci_os_image     = "{os_image}"
instance_prefix  = "oci-bot"
oci_vm_count     = 1
ampere_a1_vm_memory      = "24"
ampere_a1_cpu_core_count = "4"
'''
    (session_dir / "terraform.tfvars").write_text(content, encoding="utf-8")

def get_session_dir(session_id: str) -> Path:
    return SESSIONS_DIR / session_id

def cleanup_session(session_id: str):
    session_dir = SESSIONS_DIR / session_id
    if session_dir.exists():
        shutil.rmtree(session_dir)
