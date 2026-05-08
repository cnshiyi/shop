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
