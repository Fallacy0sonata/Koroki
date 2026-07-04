from services.tts.voice_profiles import build_voice_style


def test_build_voice_style_is_stable_for_same_request() -> None:
    style_a = build_voice_style("req-123", emotion="neutral", intensity=40)
    style_b = build_voice_style("req-123", emotion="neutral", intensity=40)
    assert style_a.variant == style_b.variant


def test_build_voice_style_changes_family_for_emotion() -> None:
    style = build_voice_style("req-456", emotion="annoyed", intensity=80)
    assert style.emotion == "annoyed"
    assert style.variant.startswith("annoyed_")


def test_build_voice_style_falls_back_to_neutral() -> None:
    style = build_voice_style("req-789", emotion="mystery", intensity=25)
    assert style.emotion == "neutral"
