def test_imports_smoke():
    import puripuly_heart  # noqa: F401
    from puripuly_heart import main  # noqa: F401
    from puripuly_heart.providers.llm import (  # noqa: F401
        gemini,
        qwen,
    )
    from puripuly_heart.providers.stt import (  # noqa: F401
        deepgram,
        qwen_asr,
    )
