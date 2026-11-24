"""나만 보는 사이트(my/)용 암호화 데이터 만들기.
사용법: MY_PASS=비밀번호 python3 tools/build_my.py 입력폴더
입력폴더: today.json upcoming.json macro.json picks.json watch.json preview/*.json
결과: my/data.enc (AES-256-GCM, PBKDF2-SHA256 250000회)"""
import base64, glob, json, os, sys
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
src = sys.argv[1]; pw = os.environ["MY_PASS"].encode()
def rd(n):
    p = os.path.join(src, n)
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None
pv = {}
for p in sorted(glob.glob(os.path.join(src, "preview", "*.json"))):
    if os.path.basename(p) == "index.json": continue
    d = json.load(open(p, encoding="utf-8"))
    if d.get("ticker"): pv[d["ticker"].upper()] = d
blob = {"today": rd("today.json"), "upcoming": rd("upcoming.json"), "macro": rd("macro.json"),
        "picks": rd("picks.json"), "watch": rd("watch.json"), "preview": pv}
raw = json.dumps(blob, ensure_ascii=False).encode()
salt, iv, it = os.urandom(16), os.urandom(12), 250000
key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=it).derive(pw)
ct = AESGCM(key).encrypt(iv, raw, None)
b = lambda x: base64.b64encode(x).decode()
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "my", "data.enc")
json.dump({"v": 1, "iter": it, "salt": b(salt), "iv": b(iv), "ct": b(ct)}, open(out, "w"))
print("ok", len(raw), "bytes,", len(pv), "previews")
