from django.utils import timezone


def append_note(existing: str | None, addition: str | None, *, unique: bool = False) -> str:
    base = str(existing or '').strip()
    next_text = str(addition or '').strip()
    if not next_text:
        return base
    if not base:
        return next_text
    if next_text.startswith(base):
        return next_text
    if unique and next_text in base.splitlines():
        return base
    return '\n'.join(part for part in [base, next_text] if part)


def prepend_note(existing: str | None, addition: str | None, *, unique: bool = False) -> str:
    base = str(existing or '').strip()
    next_text = str(addition or '').strip()
    if not next_text:
        return base
    if not base:
        return next_text
    if base.startswith(next_text):
        return base
    if unique and next_text in base.splitlines():
        return base
    return '\n'.join(part for part in [next_text, base] if part)


def with_note_time(note: str | None, *, value=None, label: str = '时间跟踪') -> str:
    text = str(note or '').strip()
    if not text:
        return ''
    dt = value or timezone.now()
    try:
        dt_text = timezone.localtime(dt).strftime('%Y-%m-%d %H:%M:%S')
    except Exception:
        dt_text = str(dt)
    return f'{label}：{dt_text}｜{text}'
