# Build with: .venv\Scripts\python.exe -m PyInstaller --noconfirm Speak.spec
from pathlib import Path
from PyInstaller.utils.hooks import collect_all

root = Path(SPECPATH)
datas = [(str(root / "upstream/config_server.py"), "upstream"), (str(root / "upstream/core"), "upstream/core"), (str(root / "upstream/LICENSE"), "licenses/CapsWriter-Offline")]
binaries = []
hiddenimports = ["win32crypt", "comtypes.gen.UIAutomationClient", "onnxruntime", "gguf", "rich", "websockets", "soundfile", "srt", "core.server.engines.qwen_asr_gguf.asr_engine", "core.server.merger.text_merger"]
for package in ("sounddevice", "soundfile", "onnxruntime", "gguf"):
    d, b, h = collect_all(package)
    datas += d; binaries += b; hiddenimports += h
a = Analysis([str(root / "main.py")], pathex=[str(root), str(root / "upstream")], binaries=binaries, datas=datas, hiddenimports=hiddenimports, excludes=["pytest", "torch", "transformers", "matplotlib", "pandas"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Speak", debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="Speak")
