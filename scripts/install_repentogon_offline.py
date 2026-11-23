"""Install verified official REPENTOGON assets into this project's lab only.

This is an offline fallback, not an official installer. The copy list and patch
manifest come from the official Launcher source/assets. The official Launcher
must still validate the result. This script never launches the game.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".local/testdeps"))

RELEASE = "1.1.3a"
RELEASE_SHA256 = "6b8d7b3bd85c1bcb44203c9ddade1b9be8472efd34b5b65116add214b838fd02"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def child(base: Path, relative: str) -> Path:
    target = (base / relative.replace("\\", "/")).resolve()
    if not target.is_relative_to(base.resolve()) or target == base.resolve():
        raise ValueError(f"Path escapes its base: {relative}")
    return target


def main() -> None:
    import bsdiff4

    assets = ROOT / ".local/repentogon-official"
    game = ROOT / ".local/isaac-lab/game"
    output = game / "Repentogon"
    patches = ROOT / ".local/REPENTOGONLauncher/launcher-data/patch"
    for local_path in (assets, game, output, patches):
        if not local_path.resolve().is_relative_to(ROOT):
            raise ValueError(f"Lab path resolves outside the workspace: {local_path}")
    lab = json.loads((game.parent / "lab.json").read_text(encoding="utf-8-sig"))
    original = Path(lab["original_game"]) / "isaac-ng.exe"
    original_hash = sha256(original)
    if original_hash.lower() != lab["original_exe_sha256"].lower():
        raise ValueError("Original EXE differs from the lab preparation record")
    expected_exe = (patches / "exehash.txt").read_text().strip().lower()
    if sha256(game / "isaac-ng.exe") != expected_exe:
        raise ValueError("Official patch does not match the lab EXE")
    archive = assets / "REPENTOGON.zip"
    if sha256(archive) != RELEASE_SHA256:
        raise ValueError("Official release ZIP hash mismatch")
    if (assets / "hash.txt").read_text().strip().lower() != RELEASE_SHA256:
        raise ValueError("Official release hash file mismatch")
    if output.exists():
        raise FileExistsError("Repentogon folder already exists; refusing to overwrite")

    # Read the actual official copy allowlist, ignoring commented-out lines.
    source = (assets / "standalone_rgon_folder.cpp").read_text(encoding="utf-8")
    array = source.split("static std::string tocopy[] = {", 1)[1].split("};", 1)[0]
    copy_list = re.findall(r'^\s*"([^"\n]+)"\s*,?\s*(?://.*)?$', array, re.MULTILINE)
    if not copy_list or copy_list[-1] != "isaac-ng.exe":
        raise ValueError("Unrecognized official copy list")
    manifest = json.loads((patches / "manifest.json").read_text())
    if set(manifest) - {"delete", "create", "patch"}:
        raise ValueError("Unknown patch manifest operations")
    for relative in manifest.get("patch", []):
        original_file = child(game, relative)
        patch_file = child(patches / "patches", relative + ".patch")
        if not original_file.is_file() or not patch_file.is_file():
            raise FileNotFoundError(relative)
        if patch_file.read_bytes()[:8] != b"BSDIFF40":
            raise ValueError(f"Unsupported official patch: {relative}")

    report = {
        "release": RELEASE,
        "release_sha256": RELEASE_SHA256,
        "source_exe_sha256": expected_exe,
        "method": "offline reproduction of official copy + BSDIFF40 manifest",
        "official_validation": "pending",
        "saves_isolated": False,
        "patches": [],
        "provenance": {
            "release": f"https://github.com/TeamREPENTOGON/REPENTOGON/releases/tag/{RELEASE}",
            "copy_list": "https://github.com/TeamREPENTOGON/Launcher/blob/main/src/standalone_rgon_folder.cpp",
            "patch_algorithm": "https://github.com/TeamREPENTOGON/Launcher/blob/main/src/diff_patcher.cpp",
        },
    }
    output.mkdir()
    # Keep a failure marker until every operation completes successfully.
    incomplete = output / "patchme.daddy"
    incomplete.touch()
    with zipfile.ZipFile(archive) as release:
        for entry in release.infolist():
            target = child(output, entry.filename)
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with release.open(entry) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
    for relative in copy_list:
        if relative.startswith("tools/"):
            continue
        src = child(game, relative)
        if src.is_file():
            dst = child(output, relative)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    for relative in manifest.get("delete", []):
        child(output, relative).unlink(missing_ok=True)
    for relative, source_relative in manifest.get("create", {}).items():
        dst = child(output, relative)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(child(patches, source_relative), dst)
    for relative in manifest.get("patch", []):
        dst = child(output, relative)
        patch = child(patches / "patches", relative + ".patch")
        before = sha256(dst)
        print(f"Applying official patch: {relative}", flush=True)
        temporary = dst.with_name(dst.name + ".patched")
        bsdiff4.file_patch(str(dst), str(temporary), str(patch))
        temporary.replace(dst)
        report["patches"].append({
            "file": relative,
            "before_sha256": before,
            "after_sha256": sha256(dst),
            "patch_sha256": sha256(patch),
        })

    # Official standalone markers. They describe the completed copy/patch
    # procedure; they do not substitute for the Launcher's DLL/version checks.
    (output / "DONT RUN THE EXE DIRECTLY.txt").write_text("1", encoding="ascii")
    (output / "steam_appid.txt").write_text("250900", encoding="ascii")
    (output / ".repentogon").touch()
    incomplete.unlink()
    if sha256(original) != original_hash:
        raise RuntimeError("Original EXE changed externally during installation")
    (game.parent / "offline-install.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Prepared offline installation: {output}")
    print("Game NOT launched. Official Launcher validation and save isolation remain required.")


if __name__ == "__main__":
    main()
