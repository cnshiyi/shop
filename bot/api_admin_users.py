"""Dashboard API views for admin user management."""

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from bot.api import (
    _admin_user_payload,
    _error,
    _ok,
    _read_payload,
    dashboard_login_required,
    dashboard_superuser_required,
)


@dashboard_superuser_required
@require_GET
def admin_users_list(request):
    User = get_user_model()
    queryset = User.objects.filter(is_staff=True).order_by('id')
    return _ok([_admin_user_payload(item) for item in queryset])


@csrf_exempt
@dashboard_superuser_required
@require_POST
def create_admin_user(request):
    User = get_user_model()
    payload = _read_payload(request)
    username = (payload.get('username') or '').strip()
    email = (payload.get('email') or '').strip()
    password = str(payload.get('password') or '').strip()
    is_active = str(payload.get('is_active', 'true')).lower() in {'1', 'true', 'yes', 'on'}
    is_superuser = str(payload.get('is_superuser', 'false')).lower() in {'1', 'true', 'yes', 'on'}
    if not username:
        return _error('管理员用户名不能为空', status=400)
    if not password:
        return _error('管理员密码不能为空', status=400)
    if User.objects.filter(username=username).exists():
        return _error('管理员用户名已存在', status=400)
    user = User(username=username, email=email, is_active=is_active, is_staff=True, is_superuser=is_superuser)
    try:
        validate_password(password, user)
    except Exception as exc:
        return _error('; '.join(exc.messages) if hasattr(exc, 'messages') else str(exc), status=400)
    user.set_password(password)
    user.save()
    return _ok(_admin_user_payload(user))


@csrf_exempt
@dashboard_superuser_required
@require_POST
def update_admin_user(request, user_id: int):
    User = get_user_model()
    user = User.objects.filter(id=user_id, is_staff=True).first()
    if not user:
        return _error('管理员不存在', status=404)
    payload = _read_payload(request)
    username = payload.get('username')
    email = payload.get('email')
    password = payload.get('password')
    is_active = payload.get('is_active')
    is_superuser = payload.get('is_superuser')
    if username is not None:
        username = str(username).strip()
        if not username:
            return _error('管理员用户名不能为空', status=400)
        exists = User.objects.filter(username=username).exclude(id=user.id).exists()
        if exists:
            return _error('管理员用户名已存在', status=400)
        user.username = username
    if email is not None:
        user.email = str(email).strip()
    if is_active is not None:
        user.is_active = str(is_active).lower() in {'1', 'true', 'yes', 'on'}
    user.is_staff = True
    if is_superuser is not None:
        user.is_superuser = str(is_superuser).lower() in {'1', 'true', 'yes', 'on'}
    if password not in (None, ''):
        password = str(password).strip()
        try:
            validate_password(password, user)
        except Exception as exc:
            return _error('; '.join(exc.messages) if hasattr(exc, 'messages') else str(exc), status=400)
        user.set_password(password)
    if request.user.id == user.id and not user.is_active:
        return _error('不能停用当前登录管理员', status=400)
    user.save()
    return _ok(_admin_user_payload(user))


@csrf_exempt
@dashboard_superuser_required
@require_POST
def delete_admin_user(request, user_id: int):
    User = get_user_model()
    user = User.objects.filter(id=user_id, is_staff=True).first()
    if not user:
        return _error('管理员不存在', status=404)
    if request.user.id == user.id:
        return _error('不能删除当前登录管理员', status=400)
    remaining = User.objects.filter(is_staff=True).exclude(id=user.id).count()
    if remaining <= 0:
        return _error('至少保留一个管理员', status=400)
    user.delete()
    return _ok(True)


@csrf_exempt
@dashboard_login_required
@require_POST
def change_my_password(request):
    payload = _read_payload(request)
    old_password = str(payload.get('old_password') or '')
    new_password = str(payload.get('new_password') or '')
    confirm_password = str(payload.get('confirm_password') or '')
    if not old_password or not new_password:
        return _error('旧密码和新密码不能为空', status=400)
    if new_password != confirm_password:
        return _error('两次输入的新密码不一致', status=400)
    if not request.user.check_password(old_password):
        return _error('旧密码不正确', status=400)
    try:
        validate_password(new_password, request.user)
    except Exception as exc:
        return _error('; '.join(exc.messages) if hasattr(exc, 'messages') else str(exc), status=400)
    request.user.set_password(new_password)
    request.user.save(update_fields=['password'])
    return _ok(True)
