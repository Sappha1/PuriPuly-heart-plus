from __future__ import annotations


def build_translation_user_message(*, text: str, context: str) -> str:
    input_block = f"<input>\n{text}\n</input>"
    if context:
        return f"<context>\n{context}\n</context>\n\n{input_block}"
    return input_block
