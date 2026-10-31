import os

import pytest

import mod


def test_mask_token_short_and_long():
    assert mod._mask_token("a") == "*"
    assert mod._mask_token("ab") == "**"
    assert mod._mask_token("hello") == "h***o"


def test_moderator_filter_urls_and_emojis():
    m = mod.Moderator({"strip_urls": True, "strip_emojis": True, "censor_slurs": False})
    assert m.filter("check 🛅 this https://example.com 😃") == "check  this [link]"


def test_moderator_filter_slurs_mask_and_drop(tmp_path):
    bl = tmp_path / "bl2.txt"
    bl.write_text("nasty\nswear\n", encoding="utf-8")

    m_mask = mod.Moderator(
        {
            "strip_urls": False,
            "strip_emojis": False,
            "censor_slurs": True,
            "blocklist_path": str(bl),
            "censor_mode": "mask",
        }
    )
    assert m_mask.filter("that nasty thing") == "that n***y thing"

    m_drop = mod.Moderator(
        {
            "strip_urls": False,
            "strip_emojis": False,
            "censor_slurs": True,
            "blocklist_path": str(bl),
            "censor_mode": "drop",
        }
    )
    assert m_drop.filter("a swear here") == "a  here"


def _masker(tmp_path, terms):
    bl = tmp_path / "bl.txt"
    bl.write_text("\n".join(terms) + "\n", encoding="utf-8")
    return mod.Moderator(
        {
            "strip_urls": False,
            "strip_emojis": False,
            "blocklist_path": str(bl),
            "censor_mode": "mask",
        }
    )


def test_a_term_inside_a_bigger_word_is_left_alone(tmp_path):
    out = _masker(tmp_path, ["ass"]).filter("first class pass, you ass")
    assert out == "first class pass, you a*s"


def test_an_accent_does_not_slip_past_the_list(tmp_path):
    out = _masker(tmp_path, ["nasty"]).filter("so nästy and café")
    assert out == "so n***y and café"


def test_a_decomposed_accent_still_matches(tmp_path):
    out = _masker(tmp_path, ["nasty"]).filter("nastý one")
    assert out == "n***ý one"


def test_adding_and_removing_a_term_keeps_the_comments(tmp_path):
    bl = tmp_path / "bl.txt"
    bl.write_text("# keep me\nnasty\n", encoding="utf-8")
    c = mod.SlurCensor(str(bl))

    c.add("rude")
    c.add("rude")
    assert bl.read_text(encoding="utf-8") == "# keep me\nnasty\nrude\n"

    c.remove("nasty")
    c.remove("never there")
    assert bl.read_text(encoding="utf-8") == "# keep me\nrude\n"
    assert c.list() == ["rude"]


def test_a_hand_edit_is_picked_up(tmp_path):
    bl = tmp_path / "bl.txt"
    bl.write_text("nasty\n", encoding="utf-8")
    c = mod.SlurCensor(str(bl))

    bl.write_text("nasty\nrude\n", encoding="utf-8")
    os.utime(bl, (c.mtime + 5, c.mtime + 5))
    assert c.list() == ["nasty", "rude"]


if __name__ == "__main__":
    pytest.main(["-q"])
