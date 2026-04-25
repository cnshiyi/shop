from dataclasses import dataclass


@dataclass(slots=True)
class ProvisionResult:
    ok: bool
    instance_id: str = ''
    public_ip: str = ''
    login_user: str = ''
    login_password: str = ''
    note: str = ''
    static_ip_name: str = ''
