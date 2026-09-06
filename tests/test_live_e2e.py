import urllib.request
import urllib.parse
import json
import base64
import time
from io import BytesIO
from PIL import Image

BASE = 'http://matrixspot.local:5000'
results = []

def record(name, passed, detail=''):
    results.append((name, passed, detail))
    status = '[PASS]' if passed else '[FAIL]'
    print(f"{status} {name}: {detail}")

def req(path, method='GET', data=None, headers=None):
    url = BASE + path
    hdrs = headers.copy() if headers else {}
    body = None
    if data is not None:
        if isinstance(data, (dict, list)):
            body = json.dumps(data).encode('utf-8')
            hdrs['Content-Type'] = 'application/json'
        elif isinstance(data, (bytes, bytearray)):
            body = data
        else:
            body = str(data).encode('utf-8')
    r = urllib.request.Request(url, data=body, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, resp.read(), resp.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers
    except Exception as e:
        return 0, str(e).encode('utf-8', errors='ignore'), {}

def main():
    print(f"Connecting to live server at {BASE}...")

    # 1. GET /
    status, body, _ = req('/')
    record('GET /', status == 200 and b'<!DOCTYPE html>' in body and b'SpotifyMatrix' in body, f"status={status}, len={len(body)}")

    # 2. GET /logs
    status, body, _ = req('/logs')
    record('GET /logs', status == 200 and b'SpotifyMatrix Logs' in body, f"status={status}, len={len(body)}")

    # 3. GET /api/state
    status, body, _ = req('/api/state')
    data = json.loads(body.decode('utf-8')) if status == 200 else {}
    expected_keys = ['title', 'artist', 'is_playing', 'display_mode', 'effective_mode', 'brightness', 'idle_mode', 'accent_name']
    record('GET /api/state', status == 200 and all(k in data for k in expected_keys), f"status={status}, track={data.get('title')!r}, mode={data.get('display_mode')}")

    init_state = dict(data)

    # 4. GET /api/frame.png
    status, body, _ = req('/api/frame.png')
    is_png = body.startswith(b'\x89PNG\r\n\x1a\n')
    record('GET /api/frame.png', status == 200 and is_png, f"status={status}, is_png={is_png}, len={len(body)}")

    # 5. GET /api/lyrics
    status, body, _ = req('/api/lyrics')
    lyr_data = json.loads(body.decode('utf-8')) if status == 200 else {}
    record('GET /api/lyrics', status == 200 and 'lyrics' in lyr_data, f"status={status}, lines={len(lyr_data.get('lyrics') or [])}")

    # 6. GET /api/logs
    status, body, _ = req('/api/logs')
    logs_arr = json.loads(body.decode('utf-8')) if status == 200 else []
    record('GET /api/logs', status == 200 and isinstance(logs_arr, list), f"status={status}, count={len(logs_arr)}")

    # 7. GET /mode query param
    status, body, _ = req('/mode?set=cd')
    record('GET /mode?set=cd (valid)', status == 200 and b'"ok": true' in body, f"status={status}")
    status, body, _ = req('/mode?set=invalid_xyz')
    record('GET /mode?set=invalid (invalid)', status == 400 and b'Invalid mode' in body, f"status={status}")

    # 8. POST /api/mode
    for m in ['default', 'cd', 'lyrics', 'art', 'clock', 'custom']:
        status, body, _ = req('/api/mode', method='POST', data={'mode': m})
        record(f"POST /api/mode ({m})", status == 200 and b'"ok": true' in body, f"status={status}")
    status, body, _ = req('/api/mode', method='POST', data={'mode': 'bogus'})
    record('POST /api/mode (bogus)', status == 400 and b'Invalid mode' in body, f"status={status}")

    # 9. POST /api/brightness
    status, body, _ = req('/api/brightness', method='POST', data={'value': 75})
    record('POST /api/brightness (75)', status == 200 and b'"brightness": 75' in body, f"status={status}")
    status, body, _ = req('/api/brightness', method='POST', data={'value': -10})
    record('POST /api/brightness (-10 clamped to 1)', status == 200 and b'"brightness": 1' in body, f"status={status}")
    status, body, _ = req('/api/brightness', method='POST', data={'value': 250})
    record('POST /api/brightness (250 clamped to 100)', status == 200 and b'"brightness": 100' in body, f"status={status}")
    status, body, _ = req('/api/brightness', method='POST', data={'value': 'not-a-num'})
    record('POST /api/brightness (not-a-number)', status == 400 and b'error' in body, f"status={status}")

    # 10. POST /api/spin-speed & text-speed
    status, body, _ = req('/api/spin-speed', method='POST', data={'value': 45.5})
    record('POST /api/spin-speed (45.5)', status == 200 and b'45.5' in body, f"status={status}")
    status, body, _ = req('/api/text-speed', method='POST', data={'value': 25})
    record('POST /api/text-speed (25)', status == 200 and b'25' in body, f"status={status}")

    # 11. POST /api/lyrics-style
    for st in ['karaoke', 'scroll', 'pop']:
        status, body, _ = req('/api/lyrics-style', method='POST', data={'value': st})
        record(f"POST /api/lyrics-style ({st})", status == 200 and f'"{st}"'.encode() in body, f"status={status}")
    status, body, _ = req('/api/lyrics-style', method='POST', data={'value': 'rap'})
    record('POST /api/lyrics-style (invalid)', status == 400 and b'Invalid style' in body, f"status={status}")

    # 12. POST /api/smart-scroll, progress-ring, art-pan
    for ep, val in [('smart-scroll', False), ('smart-scroll', True), ('progress-ring', True), ('art-pan', True)]:
        status, body, _ = req(f'/api/{ep}', method='POST', data={'value': val})
        record(f"POST /api/{ep} ({val})", status == 200 and b'"ok": true' in body, f"status={status}")

    # 13. POST /api/idle-mode
    for im in ['clock', 'plasma', 'rain', 'stars', 'life', 'fire', 'cycle']:
        status, body, _ = req('/api/idle-mode', method='POST', data={'value': im})
        record(f"POST /api/idle-mode ({im})", status == 200 and f'"{im}"'.encode() in body, f"status={status}")
    status, body, _ = req('/api/idle-mode', method='POST', data={'value': 'kaleidoscope'})
    record('POST /api/idle-mode (invalid)', status == 400 and b'Invalid idle mode' in body, f"status={status}")

    # 14. POST /api/accent-color
    for ac in ['spotify', 'sunset', 'neon', 'rose', 'arctic', 'gold', 'crimson', 'auto', 'contrast']:
        status, body, _ = req('/api/accent-color', method='POST', data={'value': ac})
        record(f"POST /api/accent-color ({ac})", status == 200 and f'"{ac}"'.encode() in body, f"status={status}")
    status, body, _ = req('/api/accent-color', method='POST', data={'value': 'custom', 'r': 120, 'g': 200, 'b': 255})
    record('POST /api/accent-color (custom RGB)', status == 200 and b'"custom"' in body, f"status={status}")
    status, body, _ = req('/api/accent-color', method='POST', data={'value': 'custom', 'r': 'bad', 'g': 200, 'b': 255})
    record('POST /api/accent-color (custom bad RGB)', status == 400 and b'error' in body, f"status={status}")
    status, body, _ = req('/api/accent-color', method='POST', data={'value': 'invalid_theme'})
    record('POST /api/accent-color (invalid theme)', status == 400 and b'Invalid theme' in body, f"status={status}")

    # 15. POST /api/sleep
    status, body, _ = req('/api/sleep', method='POST', data={'value': True})
    record('POST /api/sleep (true)', status == 200 and b'"sleeping": true' in body, f"status={status}")
    status, body, _ = req('/api/sleep', method='POST', data={'value': False})
    record('POST /api/sleep (false)', status == 200 and b'"sleeping": false' in body, f"status={status}")

    # 15b. POST /api/line-width
    status, body, _ = req('/api/line-width', method='POST', data={'value': 2})
    record('POST /api/line-width (2)', status == 200 and b'"line_width": 2' in body, f"status={status}")
    status, body, _ = req('/api/line-width', method='POST', data={'value': 99})
    record('POST /api/line-width (out of bounds)', status == 400 and b'error' in body, f"status={status}")

    # 16. POST /api/custom-media
    img = Image.new('RGB', (64, 64), color=(255, 0, 128))
    buf = BytesIO()
    img.save(buf, format='PNG')
    b64_png = 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode()
    status, body, _ = req('/api/custom-media', method='POST', data={'image_base64': b64_png})
    record('POST /api/custom-media (valid PNG)', status == 200 and b'"ok": true' in body, f"status={status}")

    status, body, _ = req('/api/custom-media', method='POST', data={'image_base64': 'corrupt-not-base64!'})
    record('POST /api/custom-media (corrupt data)', status == 400 and b'error' in body, f"status={status}")

    # 17. 404 Handlers
    status, body, _ = req('/nonexistent/page')
    record('GET 404 handling', status == 404 and body == b'Not Found', f"status={status}")
    status, body, _ = req('/api/unknown_endpoint', method='POST', data={})
    record('POST 404 handling', status == 404 and body == b'Not Found', f"status={status}")

    # 18. Malformed JSON
    status, body, _ = req('/api/mode', method='POST', data='{this is not valid json', headers={'Content-Type': 'application/json'})
    record('POST malformed JSON', status == 400 and b'Invalid mode' in body, f"status={status}")

    # 19. Empty POST body
    status, body, _ = req('/api/mode', method='POST', data='', headers={'Content-Length': '0'})
    record('POST empty body', status == 400 and b'Invalid mode' in body, f"status={status}")

    # 20. Restore initial settings
    if init_state.get('display_mode'):
        req('/api/mode', method='POST', data={'mode': init_state['display_mode']})
    if init_state.get('brightness'):
        req('/api/brightness', method='POST', data={'value': init_state['brightness']})
    if init_state.get('accent_name'):
        req('/api/accent-color', method='POST', data={'value': init_state['accent_name']})
    if init_state.get('idle_mode'):
        req('/api/idle-mode', method='POST', data={'value': init_state['idle_mode']})
    if init_state.get('lyrics_style'):
        req('/api/lyrics-style', method='POST', data={'value': init_state['lyrics_style']})
    if init_state.get('line_width'):
        req('/api/line-width', method='POST', data={'value': init_state['line_width']})

    print('=' * 60)
    passed_count = sum(1 for _, p, _ in results if p)
    print(f"Total Tests: {len(results)} | Passed: {passed_count} | Failed: {len(results) - passed_count}")

if __name__ == '__main__':
    main()
