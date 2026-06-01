import sys
import os

sys.path.insert(0, os.path.abspath("src"))
import mod
import pytest


def test_mask_token_short_and_long():
    assert mod._mask_token("a") == "*"
    assert mod._mask_token("ab") == "**"
    assert mod._mask_token("hello") == "h***o"


def test_moderator_filter_urls_and_emojis():
    m = mod.Moderator({"strip_urls": True, "strip_emojis": True, "censor_slurs": False})
    out, flags = m.filter("check 🛅 this https://example.com 😃")
    print(out)
    assert "[link]" in out
    assert flags["urls"] == 1
    assert flags["emojis"] == 1


def test_moderator_filter_slurs_mask_and_drop(tmp_path):
    bl = tmp_path / "bl2.txt"
    bl.write_text("nasty\nswear\n", encoding="utf-8")

    m_mask = mod.Moderator(
        {
            "strip_urls": False,
            "strip_emojis": False,
            "censor_slurs": True,
            "blocklist_path": str(bl),
        }
    )
    out_mask, flags_mask = m_mask.filter("that nasty thing", mode="mask")
    assert flags_mask["slurs"] == 1
    assert "n***y" in out_mask or "nasty" not in out_mask

    m_drop = mod.Moderator(
        {
            "strip_urls": False,
            "strip_emojis": False,
            "censor_slurs": True,
            "blocklist_path": str(bl),
        }
    )
    out_drop, flags_drop = m_drop.filter("a swear here", mode="drop")
    assert flags_drop["slurs"] == 1
    assert "swear" not in out_drop


if __name__ == "__main__":
    pytest.main(["-q"])
