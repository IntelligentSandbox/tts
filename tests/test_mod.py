import sys
import os
import re

sys.path.insert(0, os.path.abspath("src"))
import mod
import pytest


def test_mask_token_short_and_long():
    assert mod._mask_token("a") == "*"
    assert mod._mask_token("ab") == "**"
    assert mod._mask_token("hello") == "h***o"


def test_slur_censor_mask_drop_and_censor(tmp_path):
    bl = tmp_path / "blocklist.txt"
    bl.write_text("badword\nevil\n", encoding="utf-8")
    c = mod.SlurCensor(str(bl))

    masked, n = c._mask("this is a badword")
    assert n == 1
    assert "b*****d" in masked

    dropped, n2 = c._drop("evil things here")
    assert n2 == 1
    assert "evil" not in dropped

    out_mask, n3 = c.censor("evil and badword", mode="mask")
    assert n3 == 2
    assert re.search(r"e..l|e\*\*l|e\*l", out_mask) or "e**l" in out_mask or "e**l"
    assert "b*****d" in out_mask

    out_drop, n4 = c.censor("so evil here", mode="drop")
    assert n4 == 1
    assert "evil" not in out_drop


def test_moderator_filter_urls_and_emojis():
    m = mod.Moderator({"strip_urls": True, "strip_emojis": True, "censor_slurs": False})
    out, flags = m.filter("check this https://example.com 😃")
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

