import ftfy


def repair_unicode(value: str | None) -> str | None:
    """Repair common Unicode mojibake in generated text."""
    if not isinstance(value, str):
        return value

    return ftfy.fix_text(value)
