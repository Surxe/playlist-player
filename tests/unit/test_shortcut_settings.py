"""The shortcut settings: their defaults, how a trigger is checked, and how it reads."""

from __future__ import annotations

import pytest

from spotifixed.config import load_settings
from spotifixed.errors import ConfigError
from spotifixed.shortcut_settings import describe, from_settings, trigger_problem


def test_the_defaults_are_ctrl_and_the_volume_keys_five_at_a_time():
    up, down = from_settings(load_settings())
    assert (up.id, up.trigger, up.volume_delta) == ("volume-up", "CTRL+XF86AudioRaiseVolume", 5)
    assert (down.id, down.trigger, down.volume_delta) == (
        "volume-down", "CTRL+XF86AudioLowerVolume", -5,
    )  # fmt: skip


def test_an_empty_trigger_leaves_that_shortcut_out(monkeypatch):
    monkeypatch.setenv("SPOTIFIXED_SHORTCUT_VOLUME_UP", "")
    monkeypatch.setenv("SPOTIFIXED_SHORTCUT_VOLUME_STEP", "10")
    (down,) = from_settings(load_settings())
    assert (down.id, down.volume_delta) == ("volume-down", -10)


@pytest.mark.parametrize(
    ("name", "value", "match"),
    [
        ("SPOTIFIXED_SHORTCUT_VOLUME_UP", "ctrl+Up", "VOLUME_UP has an unknown modifier 'ctrl'"),
        ("SPOTIFIXED_SHORTCUT_VOLUME_DOWN", "CTRL+", "VOLUME_DOWN must end in one key name"),
        ("SPOTIFIXED_SHORTCUT_VOLUME_DOWN", "CTRL+Page Down", "one key name"),
        ("SPOTIFIXED_SHORTCUT_VOLUME_UP", "SHIFT+SHIFT+a", "twice"),
        ("SPOTIFIXED_SHORTCUT_VOLUME_STEP", "0", "VOLUME_STEP"),
        ("SPOTIFIXED_SHORTCUT_VOLUME_STEP", "101", "VOLUME_STEP"),
    ],
)
def test_a_bad_setting_is_refused_naming_its_variable(monkeypatch, name, value, match):
    monkeypatch.setenv(name, value)
    with pytest.raises(ConfigError, match=match):
        from_settings(load_settings())


@pytest.mark.parametrize("trigger", ["CTRL+a", "LOGO+Up", "CTRL+ALT+SHIFT+F12", "XF86AudioMute"])
def test_well_formed_triggers_pass(trigger):
    assert trigger_problem(trigger) is None


@pytest.mark.parametrize(
    ("trigger", "shown"),
    [
        ("CTRL+XF86AudioRaiseVolume", "Ctrl+Volume Up"),
        ("CTRL+XF86AudioLowerVolume", "Ctrl+Volume Down"),
        ("LOGO+SHIFT+a", "Meta+Shift+A"),
        ("ALT+F5", "Alt+F5"),
    ],
)
def test_a_trigger_is_described_the_way_a_person_reads_it(trigger, shown):
    assert describe(trigger) == shown
