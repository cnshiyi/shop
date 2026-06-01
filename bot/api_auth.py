"""Dashboard API views for authentication and current-user metadata."""

from django.contrib.auth import authenticate, login, logout
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from bot.api import (
    DASHBOARD_SESSION_IDLE_SECONDS,
    _dashboard_session_payload,
    _error,
    _generate_totp_secret,
    _json_payload,
    _normalize_totp_secret,
    _ok,
    _session_token_for_request,
    _staff_required,
    _totp_otpauth_url,
    _totp_secret,
    _verify_totp_token,
    dashboard_login_required,
    dashboard_superuser_required,
)
from core.models import SiteConfig


@csrf_exempt
@require_POST
def auth_login(request):
    payload = _json_payload(request)
    username = request.POST.get('username') or request.headers.get('x-username') or payload.get('username')
    password = request.POST.get('password') or request.headers.get('x-password') or payload.get('password')
    otp_token = request.POST.get('otp_token') or request.POST.get('otpToken') or payload.get('otp_token') or payload.get('otpToken')

    user = authenticate(request, username=username, password=password)
    if not user:
        return _error('用户名或密码错误', status=401)
    if not user.is_active:
        return _error('用户已禁用', status=403)
    if not _staff_required(user):
        return _error('没有后台权限', status=403)

    totp_secret = _totp_secret()
    if totp_secret and not _verify_totp_token(otp_token, totp_secret):
        return _error('Google 验证码错误或已过期', status=401)

    login(request, user)
    request.session.set_expiry(DASHBOARD_SESSION_IDLE_SECONDS)
    return _ok(_dashboard_session_payload(request))


@csrf_exempt
@dashboard_login_required
@require_POST
def auth_logout(request):
    logout(request)
    return _ok(True)


@csrf_exempt
@dashboard_login_required
@require_POST
def auth_refresh(request):
    return _ok(_dashboard_session_payload(request))


@dashboard_login_required
@require_GET
def auth_codes(request):
    codes = ['dashboard', 'users', 'cloud:read', 'finance:read', 'monitoring:read', 'settings:read']
    if getattr(request.user, 'is_superuser', False):
        codes.extend([
            'superuser',
            'users:write',
            'cloud:write',
            'cloud:danger',
            'finance:write',
            'settings:write',
        ])
    return _ok(codes)


@csrf_exempt
@dashboard_superuser_required
@require_POST
def auth_totp_start(request):
    payload = _json_payload(request)
    current_secret = _totp_secret()
    replacing_existing = bool(current_secret)
    if replacing_existing:
        old_token = payload.get('old_otp_token') or payload.get('oldOtpToken')
        if not _verify_totp_token(old_token, current_secret):
            return _error('更换 TOTP 密钥前，请先输入当前 Google Authenticator 的 6 位动态码', status=400)
    secret = _normalize_totp_secret(_generate_totp_secret())
    request.session['dashboard_totp_pending_secret'] = secret
    request.session['dashboard_totp_replacing_existing'] = replacing_existing
    request.session.set_expiry(10 * 60)
    username = request.user.get_username() or 'admin'
    return _ok({
        'enabled': replacing_existing,
        'otpauthUrl': _totp_otpauth_url(secret, username),
        'secret': secret,
    })


@csrf_exempt
@dashboard_superuser_required
@require_POST
def auth_totp_bind(request):
    payload = _json_payload(request)
    token = payload.get('otp_token') or payload.get('otpToken')
    secret = request.session.get('dashboard_totp_pending_secret')
    if not secret:
        return _error('请先生成 Google 验证器二维码', status=400)
    if _totp_secret() and not request.session.get('dashboard_totp_replacing_existing'):
        return _error('更换 TOTP 密钥前，请先验证当前 Google Authenticator 动态码并重新生成二维码', status=400)
    if not _verify_totp_token(token, secret):
        return _error('新 Google 验证码错误或已过期', status=400)
    SiteConfig.set('dashboard_totp_secret', secret, sensitive=True)
    request.session.pop('dashboard_totp_pending_secret', None)
    request.session.pop('dashboard_totp_replacing_existing', None)
    request.session.set_expiry(DASHBOARD_SESSION_IDLE_SECONDS)
    return _ok({'enabled': True})


@dashboard_login_required
@require_GET
def user_info(request):
    username = request.user.get_username() or 'admin'
    is_superuser = bool(getattr(request.user, 'is_superuser', False))
    return _ok({
        'userId': str(request.user.pk),
        'username': username,
        'realName': request.user.get_full_name() or username,
        'avatar': '',
        'desc': 'Shop 管理后台管理员',
        'homePath': '/admin/analytics',
        'token': _session_token_for_request(request),
        'is_superuser': is_superuser,
        'is_staff': bool(getattr(request.user, 'is_staff', False)),
        'roles': ['superuser' if is_superuser else 'staff'],
        'permissions': ['superuser', 'cloud:danger'] if is_superuser else [],
    })


@dashboard_login_required
@require_GET
def me(request):
    return _ok({
        'id': request.user.id,
        'username': request.user.get_username(),
        'is_superuser': request.user.is_superuser,
        'is_staff': request.user.is_staff,
    })
