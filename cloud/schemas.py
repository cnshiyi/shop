from dataclasses import dataclass


@dataclass(slots=True)
class ProvisionResult:
    ok: bool
    instance_id: str = ''
    public_ip: str = ''
    static_ip_name: str = ''
    login_user: str = ''
    login_password: str = ''
    note: str = ''
