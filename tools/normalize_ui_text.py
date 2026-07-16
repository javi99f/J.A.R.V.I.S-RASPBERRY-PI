"""Replace damaged legacy glyph strings with portable UI text."""

from pathlib import Path


def normalize_hud(path: Path) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    output = []
    for line in lines:
        stripped = line.strip()
        indent = line[: len(line) - len(line.lstrip())]
        if "txt, col =" in line and "MUTED" in line:
            line = indent + 'txt, col = "[X]  MUTED", qcol(C.MUTED_C)'
        elif "txt, col =" in line and "SPEAKING" in line:
            line = indent + 'txt, col = "[O]  SPEAKING", qcol(C.ACC)'
        elif stripped.startswith("sym ="):
            line = indent + 'sym = "*" if self._blink else "."'
        elif '"image":' in line and '"video":' in line:
            line = indent + '"image": ("IMG", "#00d4ff"), "video": ("VID", "#ff6b00"),'
        elif '"audio":' in line and '"pdf":' in line:
            line = indent + '"audio": ("AUD", "#cc44ff"), "pdf": ("PDF", "#ff4444"),'
        elif '"word":' in line and '"excel":' in line:
            line = indent + '"word": ("DOC", "#4488ff"), "excel": ("XLS", "#44bb44"),'
        elif '"code":' in line and '"archive":' in line:
            line = indent + '"code": ("CODE", "#ffcc00"), "archive": ("ZIP", "#ff8844"),'
        elif '"pptx":' in line and '"text":' in line:
            line = indent + '"pptx": ("PPT", "#ff6622"), "text": ("TXT", "#aaaaaa"),'
        elif '"data":' in line and '"unknown":' in line:
            line = indent + '"data": ("DATA", "#88ddff"), "unknown": ("FILE", "#888888"),'
        elif "Images " in line and "Video" in line:
            line = indent + '"Images - Video - Audio - PDF - Docs - Code - Data")'
        elif "INITIALISATION REQUIRED" in line and "_lbl" in line:
            line = indent + 'layout.addWidget(_lbl("INITIALISATION REQUIRED", 13, True))'
        elif "_or_input.setPlaceholderText" in line:
            line = indent + 'self._or_input.setPlaceholderText("sk-or-... (optional)")'
        elif "init_btn = QPushButton" in line:
            line = indent + 'init_btn = QPushButton("INITIALISE SYSTEMS")'
        elif "hdr = QLabel" in line and "SYS MONITOR" in line:
            line = indent + 'hdr = QLabel("SYS MONITOR")'
        elif "No file loaded" in line:
            line = indent + 'self._file_hint = QLabel("No file loaded - drop or click above to upload")'
        elif "MICROPHONE ACTIVE" in line:
            line = indent + 'self._mute_btn = QPushButton("MICROPHONE ACTIVE")'
        elif "FULLSCREEN" in line and "QPushButton" in line:
            line = indent + 'fs_btn = QPushButton("FULLSCREEN  [F11]")'
        elif 'lay.addWidget(_fl("[F4] Mute' in line:
            line = indent + 'lay.addWidget(_fl("[F4] Mute  -  [F11] Fullscreen"))'
        elif "Tell JARVIS what to do with it" in line:
            line = indent + 'self._file_hint.setText(f"{icon}  {p.name}  -  {size}  -  Tell JARVIS what to do with it")'
        elif "macOS" in line and "powermetrics" in line:
            line = indent + "# macOS - powermetrics (GPU Engine)"
        elif "self._value = 0.0" in line:
            line = indent + "self._value = 0.0       # 0-100"
        elif "p.drawText" in line and "cy - 24" in line:
            line = indent + 'p.drawText(QRectF(0, cy - 24, W, 32), Qt.AlignmentFlag.AlignCenter, "v")'
        elif "ext_str" in line and "size_str" in line:
            line = indent + 'f"{ext_str}  -  {size_str}")'
        elif "if len(par) > 42" in line:
            line = indent + 'if len(par) > 42: par = "..." + par[-41:]'
        elif "p.drawText" in line and "W - 34" in line:
            line = indent + 'p.drawText(QRectF(W - 34, 0, 28, H), Qt.AlignmentFlag.AlignCenter, "X")'
        output.append(line)
    path.write_text("\n".join(output) + "\n", encoding="utf-8")


def main() -> None:
    normalize_hud(Path("omar_ai_core/display/hud.py"))


if __name__ == "__main__":
    main()
