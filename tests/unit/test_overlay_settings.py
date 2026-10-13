"""The overlay's adjustable settings: the rules, layering overrides, and the daemon's store."""

from __future__ import annotations

import json
import threading

import pytest

from playlist_player.config import load_settings
from playlist_player.errors import ConfigError
from playlist_player.overlay.main import LiveSettings, placement_and_style
from playlist_player.overlay.presenter import OverlayModel
from playlist_player.overlay_settings import (
    RULES,
    OverlaySettingError,
    OverlaySettings,
    clean,
    env_lines,
    env_name,
    prune,
    salvage,
)
from playlist_player.server.overlay_config import OverlayConfig


@pytest.fixture
def defaults() -> OverlaySettings:
    return OverlaySettings.from_settings(load_settings())


# ---------------------------------- rules ---------------------------------- #


def test_every_overlay_option_in_the_schema_is_adjustable_and_named(defaults):
    settings = load_settings()
    overlay_fields = {name for name in type(settings).__slots__ if name.startswith("overlay_")}
    assert {f"overlay_{key}" for key in RULES} == overlay_fields
    assert env_name("margin_y") == "PLAYLIST_PLAYER_OVERLAY_MARGIN_Y"
    assert defaults.margin_y == 120 and defaults.anchor == "bottom" and defaults.font == "Sans Bold"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("anchor", "top-left"), ("margin_x", -40), ("margin_y", 0), ("monitor", ""),
        ("monitor", "HDMI-A-1"), ("font_size", 48), ("max_width", 800), ("font", "Serif Italic"),
        ("font", "DejaVu Sans, Bold 1.5"), ("color", "#fe0"), ("outline_color", "#11223344"),
        ("color", "white"), ("lines", 2), ("lead_ms", -200), ("fade_ms", 0),
    ],
)  # fmt: skip
def test_values_a_setting_may_take(key, value):
    assert clean(key, value) == value


@pytest.mark.parametrize(
    ("key", "value", "says"),
    [
        ("anchor", "middle", "one of top, bottom"),
        ("margin_x", 5001, "whole number from -5000 to 5000"),
        ("margin_x", 1.5, "whole number"),
        ("margin_x", "12", "whole number"),
        ("margin_x", True, "whole number"),  # a JSON true is not the number 1
        ("font_size", 0, "from 4 to 300"),
        ("lines", 3, "from 1 to 2"),
        ("fade_ms", -1, "from 0 to 5000"),
        ("monitor", "DP 2; reboot", "output connector"),
        ("color", "red; } * { color: blue", "a colour like #RRGGBB"),
        ("color", 255, "a colour like #RRGGBB"),
        ("font", "", "font name"),
        ("font", 'Sans" $(id)', "font name"),
        ("font", "Sans`id`", "font name"),
        ("font", "x" * 81, "font name"),
        ("volume", 3, "not an overlay setting"),
    ],
)
def test_values_a_setting_refuses(key, value, says):
    with pytest.raises(OverlaySettingError, match=says):
        clean(key, value)


@pytest.mark.parametrize(
    ("name", "value", "says"),
    [
        ("OVERLAY_COLOR", "red; }", "PLAYLIST_PLAYER_OVERLAY_COLOR must be a colour"),
        ("OVERLAY_FONT_SIZE", "0", "PLAYLIST_PLAYER_OVERLAY_FONT_SIZE must be a whole number"),
        ("OVERLAY_FONT", "Sans $(id)", "PLAYLIST_PLAYER_OVERLAY_FONT must be a font name"),
        ("OVERLAY_MARGIN_Y", "99999", "PLAYLIST_PLAYER_OVERLAY_MARGIN_Y must be"),
    ],
)
def test_a_bad_configured_value_fails_naming_its_variable(monkeypatch, name, value, says):
    monkeypatch.setenv(f"PLAYLIST_PLAYER_{name}", value)
    with pytest.raises(ConfigError, match=says):
        OverlaySettings.from_settings(load_settings())


def test_overrides_layer_over_the_configured_values_and_are_checked(defaults):
    merged = defaults.merged({"anchor": "top", "font_size": 20})
    assert (merged.anchor, merged.font_size, merged.margin_y) == ("top", 20, 120)
    assert defaults.merged({}) == defaults
    with pytest.raises(OverlaySettingError):
        defaults.merged({"font_size": "huge"})


def test_an_override_equal_to_the_configured_value_is_not_an_override(defaults):
    assert prune(defaults, {"margin_y": 120, "margin_x": 30, "font": "Sans Bold"}) == {
        "margin_x": 30
    }


def test_stored_overrides_keep_what_is_usable_and_report_the_rest():
    kept, notes = salvage({"margin_x": 30, "font_size": "big", "gone_setting": 1})
    assert kept == {"margin_x": 30}
    assert len(notes) == 2 and all("ignoring" in note for note in notes)
    assert salvage(["not", "an", "object"])[0] == {}
    assert salvage(None)[0] == {}


def test_settings_lines_cover_only_what_differs_and_quote_what_needs_it(defaults):
    lines = env_lines(
        defaults,
        {"font": "Serif Italic", "margin_y": 200, "anchor": "bottom", "monitor": "DP-2"},
    )
    assert lines == [
        "PLAYLIST_PLAYER_OVERLAY_MARGIN_Y=200",
        "PLAYLIST_PLAYER_OVERLAY_MONITOR=DP-2",
        'PLAYLIST_PLAYER_OVERLAY_FONT="Serif Italic"',
    ]
    assert env_lines(defaults, {}) == []


def test_settings_lines_read_back_as_the_same_values(defaults, tmp_path, monkeypatch):
    """The lines are for pasting into a settings file; a file of them must mean the same."""
    overrides = {"font": "DejaVu Sans, Bold", "color": "#ffe066", "monitor": "DP-2",
                 "anchor": "top-right", "margin_x": -15, "lines": 2}  # fmt: skip
    env_file = tmp_path / "settings.env"
    env_file.write_text("\n".join(env_lines(defaults, overrides)) + "\n")
    assert OverlaySettings.from_settings(load_settings(env_file=env_file)) == defaults.merged(
        overrides
    )
    # And through a shell, which is how a launcher may read the same file.
    import subprocess

    script = f'set -a; . "{env_file}"; printf "%s|%s" "$PLAYLIST_PLAYER_OVERLAY_FONT" '
    script += '"$PLAYLIST_PLAYER_OVERLAY_COLOR"'
    shown = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True)
    assert shown.stdout == "DejaVu Sans, Bold|#ffe066"


# --------------------------------- the store -------------------------------- #


def test_the_document_carries_configured_values_overrides_and_the_result(defaults, store):
    config = OverlayConfig(defaults, store)
    first = config.snapshot()
    assert first["overrides"] == {} and first["settings"] == first["defaults"]
    assert (first["clients"], first["preview_until_ms"], first["env_lines"]) == (0, 0, [])

    after = config.update({"margin_y": 300, "anchor": "top"})
    assert after["version"] == first["version"] + 1
    assert after["overrides"] == {"margin_y": 300, "anchor": "top"}
    assert after["settings"]["margin_y"] == 300 and after["defaults"]["margin_y"] == 120
    assert after["env_lines"] == [
        "PLAYLIST_PLAYER_OVERLAY_ANCHOR=top",
        "PLAYLIST_PLAYER_OVERLAY_MARGIN_Y=300",
    ]
    assert set(config.describe()["rules"]) == set(RULES)
    assert config.describe()["rules"]["margin_x"] == {"kind": "int", "min": -5000, "max": 5000}


def test_overrides_are_stored_and_survive_a_restart(defaults, store):
    OverlayConfig(defaults, store).update({"margin_y": 300, "font": "Serif"})
    assert json.loads(store.overlay_json.read_text()) == {
        "overrides": {"margin_y": 300, "font": "Serif"}
    }
    assert OverlayConfig(defaults, store).snapshot()["overrides"] == {
        "margin_y": 300, "font": "Serif"
    }  # fmt: skip


def test_once_a_value_is_the_configured_one_its_override_is_gone(defaults, store):
    """Copy the lines into the settings file, restart, and the file is what counts again."""
    OverlayConfig(defaults, store).update({"margin_y": 300, "margin_x": 25})
    reconfigured = defaults.merged({"margin_y": 300})
    assert OverlayConfig(reconfigured, store).snapshot()["overrides"] == {"margin_x": 25}


def test_null_drops_one_override_and_reset_drops_all(defaults, store):
    config = OverlayConfig(defaults, store)
    config.update({"margin_y": 300, "margin_x": 25, "lines": 2})
    assert config.update({"margin_x": None})["overrides"] == {"margin_y": 300, "lines": 2}
    assert config.update({"margin_y": 120})["overrides"] == {"lines": 2}  # set back by hand
    assert config.reset()["overrides"] == {}
    assert json.loads(store.overlay_json.read_text()) == {"overrides": {}}


def test_an_update_applies_whole_or_not_at_all(defaults, store):
    config = OverlayConfig(defaults, store)
    before = config.snapshot()
    with pytest.raises(OverlaySettingError, match="font_size"):
        config.update({"margin_y": 300, "font_size": 9999})
    with pytest.raises(OverlaySettingError, match="not an overlay setting"):
        config.update({"margin_y": 300, "nonsense": None})
    assert config.snapshot() == before and not store.overlay_json.exists()


def test_a_failed_save_changes_nothing(defaults, store, monkeypatch):
    config = OverlayConfig(defaults, store)

    def refuse(path, data):
        raise OSError("disk full")

    monkeypatch.setattr(store, "write_json", refuse)
    with pytest.raises(OSError, match="disk full"):
        config.update({"margin_y": 300})
    assert config.snapshot()["overrides"] == {} and config.version == 1


def test_a_damaged_or_foreign_file_does_not_stop_the_daemon(defaults, store, capsys):
    store.overlay_json.parent.mkdir(parents=True)
    store.overlay_json.write_text('{"overrides": {"margin_x": 40, "font_size": "big", "x": 1}}')
    assert OverlayConfig(defaults, store).snapshot()["overrides"] == {"margin_x": 40}
    assert capsys.readouterr().err.count("ignoring a stored overlay override") == 2

    store.overlay_json.write_text("{ this is not json")
    assert OverlayConfig(defaults, store).snapshot()["overrides"] == {}
    assert "starting without overlay overrides" in capsys.readouterr().err

    store.overlay_json.write_text("[1, 2]")
    assert OverlayConfig(defaults, store).snapshot()["overrides"] == {}


def test_listeners_hear_real_changes_only(defaults, store):
    config = OverlayConfig(defaults, store)
    heard: list[int] = []
    unsubscribe = config.subscribe(lambda: heard.append(config.version))
    config.update({"margin_y": 300})
    config.update({"margin_y": 300})  # the same again: nothing changed
    config.reset()
    config.reset()
    assert heard == [2, 3]
    unsubscribe()
    config.update({"margin_y": 1})
    assert heard == [2, 3]


def test_a_listener_that_raises_does_not_break_the_change_or_the_others(defaults):
    config = OverlayConfig(defaults)
    heard: list[int] = []
    config.subscribe(lambda: 1 / 0)
    config.subscribe(lambda: heard.append(1))
    assert config.update({"lines": 2})["overrides"] == {"lines": 2}
    assert heard == [1]


def test_a_preview_has_an_end_and_is_never_stored(defaults, store):
    now = [1000.0]
    config = OverlayConfig(defaults, store, clock=lambda: now[0])
    assert config.preview(8)["preview_until_ms"] == 1_008_000
    assert config.preview(9999)["preview_until_ms"] == 1_060_000  # capped
    assert config.preview(0)["preview_until_ms"] == 0
    assert not store.overlay_json.exists()


def test_attached_overlays_are_counted(defaults):
    config = OverlayConfig(defaults)
    config.attach()
    config.attach()
    assert config.snapshot()["clients"] == 2
    config.detach()
    config.detach()
    config.detach()  # one too many must not go negative
    assert config.snapshot()["clients"] == 0


def test_concurrent_updates_all_land(defaults, store):
    config = OverlayConfig(defaults, store)
    keys = ["margin_x", "margin_y", "font_size", "max_width", "lead_ms", "fade_ms"]
    threads = [
        threading.Thread(target=lambda k=key: [config.update({k: 200 + i}) for i in range(20)])
        for key in keys
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert config.snapshot()["overrides"] == {key: 219 for key in keys}
    assert json.loads(store.overlay_json.read_text())["overrides"] == {key: 219 for key in keys}


# ------------------------- the overlay's side of it ------------------------- #


class RecordingRenderer:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def reconfigure(self, placement, style, monitor) -> None:
        self.calls.append((placement, style, monitor))


def test_the_overlay_applies_overrides_over_its_own_starting_values(defaults):
    model, renderer = OverlayModel(lead_ms=150), RecordingRenderer()
    live = LiveSettings(defaults, model, renderer)

    live.apply({"version": 1, "overrides": {}, "preview_until_ms": 0})
    assert renderer.calls == []  # nothing differs from how it was started

    live.apply({"version": 2, "overrides": {"anchor": "top-left", "margin_x": 60, "lines": 2,
                                            "font_size": 20, "monitor": "DP-2"}})  # fmt: skip
    wanted = defaults.merged({"anchor": "top-left", "margin_x": 60, "lines": 2,
                              "font_size": 20, "monitor": "DP-2"})  # fmt: skip
    assert renderer.calls == [(*placement_and_style(wanted), "DP-2")]
    assert model._show_next is True

    live.apply({"version": 3, "overrides": {"anchor": "top-left", "margin_x": 60, "lines": 2,
                                            "font_size": 20, "monitor": "DP-2"}})  # fmt: skip
    assert len(renderer.calls) == 1  # the same settings again: nothing to redo

    live.apply({"version": 4, "overrides": {}})
    assert renderer.calls[-1] == (*placement_and_style(defaults), "")
    assert model._show_next is False


def test_the_overlay_skips_what_it_cannot_use_and_follows_the_preview(defaults):
    model, renderer = OverlayModel(), RecordingRenderer()
    live = LiveSettings(defaults, model, renderer)
    live.apply({"overrides": {"margin_y": 200, "a_newer_setting": 5, "font_size": "big"},
                "preview_until_ms": 5000})  # fmt: skip
    assert renderer.calls == [(*placement_and_style(defaults.merged({"margin_y": 200})), "")]
    model.set_connected(True)
    assert model.view(4999).visible and not model.view(5000).visible

    live.apply({"overrides": "nonsense", "preview_until_ms": "soon"})
    assert renderer.calls[-1] == (*placement_and_style(defaults), "")
    assert not model.view(4999).visible
