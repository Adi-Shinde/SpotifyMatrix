"""Exercise actual HTTP edits, durable acknowledgements, and a fresh boot."""
import base64
import concurrent.futures
from io import BytesIO
import json
from pathlib import Path
import socket
import subprocess
import sys
import threading
import urllib.error
import urllib.request

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import spotify_matrix as sm


def media(animated=False):
    image = Image.new("RGB", (64, 64), (210, 20, 70))
    output = BytesIO()
    if animated:
        image.save(output, format="GIF", save_all=True,
                   append_images=[Image.new("RGB", (64, 64), (20, 60, 220))],
                   duration=150, loop=0)
    else:
        image.save(output, format="PNG")
    return base64.b64encode(output.getvalue()).decode()


@pytest.fixture
def panel(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    args = sm.build_parser().parse_args(["--settings", str(tmp_path / "settings.json")])
    state = sm.SharedPlaybackState()
    class Display:
        def set_brightness(self, value):
            pass
    server = sm.start_control_server(port, state, threading.Lock(), Display(), args)
    assert server is not None
    def request(endpoint, body=None, method=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"http://127.0.0.1:{port}{endpoint}", data=data,
                                     headers={"Content-Type": "application/json"},
                                     method=method or ("POST" if body is not None else "GET"))
        try:
            response = urllib.request.urlopen(req, timeout=8)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            return response.status, json.loads(response.read())
    yield request, args.settings, state, args
    server.shutdown()
    server.server_close()


def test_every_panel_setting_is_durable_before_success_and_restored_in_new_process(panel):
    request, path, state, args = panel
    edits = {
        "mode": ({"mode": "art"}, "display_mode", "art"),
        "idle-mode": ({"value": "fire"}, "idle_mode", "fire"),
        "brightness": ({"value": 42}, "brightness", 42),
        "spin-speed": ({"value": 27}, "spin_speed", 27),
        "text-speed": ({"value": 31}, "text_scroll_speed", 31),
        "lyrics-style": ({"value": "karaoke"}, "lyrics_style", "karaoke"),
        "smart-scroll": ({"value": False}, "smart_scroll", False),
        "scroll-font-size": ({"value": 12}, "scroll_font_size", 12),
        "pop-font-size": ({"value": 11}, "pop_font_size", 11),
        "lyrics-lead": ({"value": 250}, "lyrics_lead_ms", 250),
        "cd-duration": ({"value": 35}, "cd_duration", 35),
        "progress-ring": ({"value": False}, "progress_ring", False),
        "art-pan": ({"value": True}, "art_pan", True),
        "line-width": ({"value": 3}, "line_width", 3),
        "sleep": ({"value": True}, "sleeping", True),
        "accent-color": ({"value": "custom", "r": 12, "g": 90, "b": 230}, "accent_color", [12, 90, 230]),
    }
    for endpoint, (body, key, expected) in edits.items():
        status, result = request("/api/" + endpoint, body)
        assert status == 200 and result["saved"] is True, (endpoint, result)
        # No sleep/debounce: unplugging after the acknowledgement must not
        # depend on a background thread getting another time slice.
        assert json.loads(path.read_text())[key] == expected, endpoint
    prefs = {"preview_on": False, "advanced_open": True, "lyrics_open": True,
             "slate_text": "Hello matrix", "slate_color": "#ab12ef"}
    status, result = request("/api/panel-preferences", {**prefs, "image_base64": media()})
    assert status == 200 and result["saved"]
    assert request("/api/save-settings", {}, "POST")[1]["saved"]
    script = """
import json,sys
from pathlib import Path
import spotify_matrix as sm
args=sm.build_parser().parse_args(['--settings',sys.argv[1], '--brightness','65','--prefer-saved-settings'])
state=sm.create_playback_state(args)
print(json.dumps({k:getattr(state,k) for k in sm.PERSISTED_FIELDS}))
"""
    result = subprocess.run([sys.executable, "-c", script, str(path)],
                            cwd=Path(sm.__file__).parent, text=True, capture_output=True, check=True)
    restored = json.loads(result.stdout.splitlines()[-1])
    for _, key, value in edits.values():
        assert restored[key] == value
    assert restored["panel_preferences"] == prefs
    assert restored["slate_draft_media"]
    assert restored["display_mode"] == "art"


@pytest.mark.parametrize("animated", [False, True])
def test_custom_media_survives_restart_with_separate_editor_draft(panel, animated):
    request, path, _, _ = panel
    assert request("/api/custom-media", {"image_base64": media(animated)})[1]["saved"]
    assert request("/api/panel-preferences", {"image_base64": "", "slate_text": "next draft"})[1]["saved"]
    restored = sm.SharedPlaybackState()
    assert sm.apply_saved_settings(path, restored)
    assert restored.display_mode == "custom"
    assert len(restored.custom_slate_frames) == (2 if animated else 1)
    assert restored.custom_slate_frames[0].getpixel((0, 0)) == (210, 20, 70)
    if animated:
        assert restored.custom_slate_frame_delay == pytest.approx(0.15)
    assert restored.slate_draft_media == ""
    assert restored.panel_preferences["slate_text"] == "next draft"


def test_failed_disk_save_is_reported_and_can_be_retried(panel, monkeypatch):
    request, path, _, _ = panel
    original = sm._atomic_write_json
    def failed(*args):
        raise OSError("disk full")
    monkeypatch.setattr(sm, "_atomic_write_json", failed)
    status, data = request("/api/brightness", {"value": 43})
    assert status == 503 and not data["saved"]
    assert not path.exists()
    monkeypatch.setattr(sm, "_atomic_write_json", original)
    assert request("/api/save-settings", {})[1]["saved"]
    assert json.loads(path.read_text())["brightness"] == 43


def test_concurrent_changes_and_reset_are_saved_consistently(panel):
    request, path, _, _ = panel
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as workers:
        results = list(workers.map(lambda value: request("/api/brightness", {"value": value}), range(30, 42)))
    assert all(status == 200 and result["saved"] for status, result in results)
    state = request("/api/state")[1]
    assert json.loads(path.read_text())["brightness"] == state["brightness"]
    request("/api/custom-media", {"image_base64": media()})
    request("/api/panel-preferences", {"advanced_open": True, "slate_text": "draft"})
    assert request("/api/reset", {})[1]["saved"]
    restored = sm.SharedPlaybackState()
    sm.apply_saved_settings(path, restored)
    assert restored.display_mode == "default"
    assert restored.brightness == 65
    assert restored.panel_preferences == sm.SharedPlaybackState().panel_preferences
    assert not restored.custom_slate_media


def test_invalid_requests_do_not_overwrite_saved_settings(panel):
    request, path, _, _ = panel
    request("/api/brightness", {"value": 49})
    original = path.read_bytes()
    for endpoint, body in [
        ("/api/mode", {"mode": "invalid"}),
        ("/api/panel-preferences", {"preview_on": "false"}),
        ("/api/panel-preferences", {"slate_color": "oops"}),
        ("/api/custom-media", {"image_base64": "invalid"}),
    ]:
        assert request(endpoint, body)[0] == 400
        assert path.read_bytes() == original


@pytest.mark.parametrize("endpoint,key,values", [
    ("mode", "display_mode", ["default", "cd", "lyrics", "art", "clock"]),
    ("idle-mode", "idle_mode", sm.IDLE_MODES),
    ("lyrics-style", "lyrics_style", ["scroll", "pop", "karaoke"]),
    ("accent-color", "accent_name", [*sm.COLOR_THEMES, "auto", "contrast"]),
])
def test_each_mode_style_and_color_choice_restores(panel, endpoint, key, values):
    request, path, _, _ = panel
    for value in values:
        body = {"mode" if endpoint == "mode" else "value": value}
        assert request("/api/" + endpoint, body)[1]["saved"]
        restored = sm.SharedPlaybackState()
        sm.apply_saved_settings(path, restored)
        assert getattr(restored, key) == value


def test_no_settings_run_never_claims_a_durable_save(panel):
    request, path, _, args = panel
    args.no_settings = True
    assert request("/api/brightness", {"value": 44})[1]["saved"] is False
    assert not path.exists()


def test_interactive_cli_override_and_service_saved_precedence(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    state = sm.SharedPlaybackState(brightness=42)
    sm.save_settings(path, state, threading.Lock())
    argv = ["--settings", str(path), "--brightness", "65"]
    monkeypatch.setattr(sys, "argv", ["spotify_matrix.py", *argv])
    assert sm.create_playback_state(sm.build_parser().parse_args(argv)).brightness == 65
    argv.append("--prefer-saved-settings")
    assert sm.create_playback_state(sm.build_parser().parse_args(argv)).brightness == 42
