"""Copy the Cursor usage mod and attach it to a cloned layout.

The script never edits the layout that was on screen, keys.json, or Insights'
files. Uninstall removes only the clone and the files whose bytes still match
the recorded hashes.
"""

import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
import time
from pathlib import Path

OWNER = "cursor-usage"
OWNED_FILES = ("main.js", "ui.js", "cache.js", "mod.json", "cursor_usage.py", "cursor.svg")
BUTTON_ID = "cursor-usage-toolbar-button"
NAME = __import__("re").compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")
PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = PACKAGE_ROOT / "mods" / "cursor-usage"


def app_home(explicit=None):
    if explicit:
        return Path(explicit).expanduser().resolve()
    override = os.environ.get("HERDR_APP_HOME")
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".config" / "herdr-app"


def mod_dir(home):
    return home / "mods" / "cursor-usage"


def data_dir(home):
    return home / "data" / "cursor-usage"


def manifest_path(home):
    return data_dir(home) / "install.json"


def layout_path(home, name):
    assert_name(name)
    return home / "layouts" / f"{name}.json"


def state_path(home, name):
    assert_name(name)
    return home / "layouts" / ".state" / f"{name}.json"


def assert_name(name):
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise RuntimeError(f"Unsupported layout name: {name!r}")


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def lstat_kind(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(info.st_mode):
        return "link"
    if stat.S_ISREG(info.st_mode):
        return "file"
    if stat.S_ISDIR(info.st_mode):
        return "dir"
    return "other"


def refuse_link(path):
    if lstat_kind(path) == "link":
        raise RuntimeError(f"Refusing a symbolic link: {path}")


def read_json(path):
    refuse_link(path)
    kind = lstat_kind(path)
    if kind is None:
        return None
    if kind != "file":
        raise RuntimeError(f"Not a regular file: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def write_atomic(path, data):
    refuse_link(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.chmod(temporary, 0o644)
    os.replace(temporary, path)


def toolbar_button():
    return {
        "id": BUTTON_ID,
        "type": "block",
        "size": {"px": 88},
        "content": {"mod": "cursor-usage", "props": {"mode": "toolbar"}},
    }


def _walk(node, parent=None):
    yield node, parent
    for child in node.get("children") or []:
        if isinstance(child, dict):
            yield from _walk(child, node)


def with_toolbar(layout):
    """Return a copy of a layout with the Cursor usage button beside tabs."""
    tree = json.loads(json.dumps(layout))
    if any(node.get("id") == BUTTON_ID for node, _parent in _walk(tree)):
        return tree
    tabs = [
        (node, parent)
        for node, parent in _walk(tree)
        if isinstance(node.get("content"), dict) and node["content"].get("mod") == "tabs"
    ]
    if len(tabs) != 1:
        raise RuntimeError('Expected one native "tabs" block. This layout cannot be integrated safely.')
    node, parent = tabs[0]
    button = toolbar_button()
    if parent and parent.get("type") == "row":
        parent.setdefault("children", []).append(button)
        return tree
    if node.get("children"):
        raise RuntimeError("The tabs block is not inside a row, so the button cannot be added safely.")
    content = node.pop("content")
    node["type"] = "row"
    node["resizable"] = False
    node["children"] = [
        {
            "id": "cursor-usage-original-tabs",
            "type": "block",
            "size": {"weight": 1},
            "content": content,
        },
        button,
    ]
    return tree


def run_herdr(args, env=None):
    result = subprocess.run(["herdr-app", *args], capture_output=True, text=True, env=env)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(detail or f"herdr-app {' '.join(args)} failed")
    return result.stdout


def staged_check():
    staging = Path(tempfile.mkdtemp(prefix="cursor-usage-check-"))
    try:
        target = staging / "mods" / "cursor-usage"
        target.mkdir(parents=True)
        for name in OWNED_FILES:
            shutil.copyfile(SOURCE_DIR / name, target / name)
        env = os.environ.copy()
        env["HERDR_APP_HOME"] = str(staging)
        env["HOME"] = str(staging)
        run_herdr(["check", str(target)], env=env)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def source_files():
    files = {}
    for name in OWNED_FILES:
        path = SOURCE_DIR / name
        if not path.is_file():
            raise RuntimeError(f"Package is missing {path}")
        files[name] = path.read_bytes()
    return files


def read_manifest(home):
    path = manifest_path(home)
    payload = read_json(path)
    if payload is None:
        return None
    if payload.get("version") != 1 or payload.get("owner") != OWNER:
        raise RuntimeError(f"Invalid install record: {path}")
    if not isinstance(payload.get("files"), dict):
        raise RuntimeError(f"Invalid install record: {path}")
    assert_name(payload.get("source"))
    assert_name(payload.get("layout"))
    return payload


def free_layout_name(home, source):
    for number in range(1, 101):
        suffix = "" if number == 1 else f"-{number}"
        name = f"{source}-cursor-usage{suffix}"
        if not NAME.fullmatch(name):
            continue
        if lstat_kind(layout_path(home, name)) or lstat_kind(state_path(home, name)):
            continue
        return name
    raise RuntimeError("No free Cursor usage layout name; existing layouts were left alone.")


def prepare_targets(home, files, force):
    target = mod_dir(home)
    refuse_link(target)
    kind = lstat_kind(target)
    manifest = read_manifest(home)
    if kind == "other":
        raise RuntimeError(f"Target mod path is not a directory: {target}")
    if kind == "dir" and manifest is None:
        raise RuntimeError(
            f"Refusing an unowned folder at {target}. Move it aside or remove it before installing."
        )
    if kind is None and manifest is not None:
        raise RuntimeError(f"Install record exists but {target} is missing.")
    issues = []
    for name, data in files.items():
        path = target / name
        refuse_link(path)
        file_kind = lstat_kind(path)
        if file_kind is None:
            continue
        if file_kind != "file":
            issues.append(f"Refusing to replace a non-regular file: {path}")
            continue
        current = sha256_file(path)
        incoming = sha256_bytes(data)
        if current == incoming:
            continue
        owned = (manifest or {}).get("files", {}).get(name)
        if owned is None:
            issues.append(f"Unowned target file would be replaced: {path}")
        elif current != owned and not force:
            issues.append(f"Owned file was edited locally: {path} (use --force to replace it)")
    if issues:
        raise RuntimeError("Refusing to install; no target files were changed:\n- " + "\n- ".join(issues))
    return manifest


def apply_files(home, files):
    target = mod_dir(home)
    target.mkdir(parents=True, exist_ok=True)
    written = []
    for name, data in files.items():
        path = target / name
        refuse_link(path)
        if lstat_kind(path) == "file" and sha256_file(path) == sha256_bytes(data):
            continue
        write_atomic(path, data)
        written.append(name)
    return written


def remove_owned_files(home, files):
    """Delete owned files that still match. Return hashes that had to stay."""
    target = mod_dir(home)
    remaining = {}
    removed = []
    for name, digest in files.items():
        path = target / name
        if lstat_kind(path) == "link":
            print(f"Kept {path} (symbolic link).")
            remaining[name] = digest
            continue
        if lstat_kind(path) is None:
            continue
        if lstat_kind(path) != "file":
            print(f"Kept {path} (not a regular file).")
            remaining[name] = digest
            continue
        if sha256_file(path) != digest:
            print(f"Kept {path} (edited locally).")
            remaining[name] = digest
            continue
        path.unlink()
        removed.append(name)
    return removed, remaining


def remove_generated(home):
    """Drop Herdr's generated editor files and the mod folder once it is empty.

    Herdr can rewrite a .d.ts as the folder disappears, so this tries twice.
    """
    target = mod_dir(home)
    for attempt in range(2):
        if lstat_kind(target) != "dir":
            return
        for path in list(target.iterdir()):
            if path.name != "jsconfig.json" and not path.name.endswith(".d.ts"):
                continue
            if lstat_kind(path) != "file":
                print(f"Kept {path} (not a regular file).")
                continue
            path.unlink()
        try:
            target.rmdir()
        except OSError:
            if attempt == 0:
                time.sleep(0.4)
                continue
        else:
            return


def current_layout(home):
    # status talks to the running app for this app home.
    env = os.environ.copy()
    env["HERDR_APP_HOME"] = str(home)
    payload = json.loads(run_herdr(["status"], env=env))
    name = payload.get("layout")
    assert_name(name)
    return name


def switch_layout(home, name):
    env = os.environ.copy()
    env["HERDR_APP_HOME"] = str(home)
    run_herdr(["do", "layout.switch", json.dumps({"name": name})], env=env)
    if current_layout(home) != name:
        raise RuntimeError(f"Herdr did not switch to {name}.")


def clone_layout(home, source, name):
    env = os.environ.copy()
    env["HERDR_APP_HOME"] = str(home)
    run_herdr(["do", "layout.clone", json.dumps({"name": source, "as": name})], env=env)
    if current_layout(home) != name:
        raise RuntimeError(f"Herdr did not switch to the new layout {name}.")


def layout_bytes(layout):
    return (json.dumps(layout, indent=2) + "\n").encode("utf-8")


def install(home, force=False):
    files = source_files()
    print("Checking the mod in a temporary Herdr home…")
    staged_check()
    print("herdr-app check passed (staged source).")
    manifest = prepare_targets(home, files, force)
    written = apply_files(home, files)
    created = None
    restore = None
    try:
        if manifest and lstat_kind(layout_path(home, manifest["layout"])) == "file":
            record = manifest
            print(f"Updated {len(written)} file(s). Left layout {record['layout']} unchanged.")
        else:
            source = manifest["source"] if manifest else current_layout(home)
            if lstat_kind(layout_path(home, source)) != "file":
                raise RuntimeError(f"Layout not found: {layout_path(home, source)}")
            name = manifest["layout"] if manifest else free_layout_name(home, source)
            if name == source:
                raise RuntimeError("Refusing to edit the layout that is already on screen.")
            created = name
            restore = source
            clone_layout(home, source, name)
            try:
                cloned = read_json(layout_path(home, name))
                if cloned is None:
                    raise RuntimeError(f"Herdr did not create {layout_path(home, name)}")
                updated = with_toolbar(cloned)
                data = layout_bytes(updated)
                write_atomic(layout_path(home, name), data)
                record = {
                    "version": 1,
                    "owner": OWNER,
                    "source": source,
                    "layout": name,
                    "layoutHash": sha256_bytes(data),
                    "files": {},
                }
                print(f"Added the Cursor usage button on layout {name}. Source layout {source} was not edited.")
            except Exception:
                try:
                    switch_layout(home, source)
                except Exception:
                    pass
                raise
        record["files"] = {name_: sha256_bytes(body) for name_, body in files.items()}
        if "layoutHash" not in record:
            raise RuntimeError("Install record is missing the layout hash.")
        write_atomic(manifest_path(home), (json.dumps(record, indent=2) + "\n").encode("utf-8"))
        print(f"Recorded ownership in {manifest_path(home)}.")
    except Exception:
        if manifest is None:
            if created and restore:
                try:
                    if current_layout(home) == created:
                        switch_layout(home, restore)
                except Exception:
                    pass
            remove_owned_files(home, {name_: sha256_bytes(body) for name_, body in files.items()})
            remove_generated(home)
            if created:
                for path in (layout_path(home, created), state_path(home, created)):
                    if lstat_kind(path) == "file":
                        path.unlink()
        raise


def uninstall(home):
    manifest = read_manifest(home)
    target = mod_dir(home)
    if manifest is None:
        if lstat_kind(target):
            raise RuntimeError(
                f"No install record at {manifest_path(home)}; refusing to remove unowned files in {target}."
            )
        print("Cursor usage is not installed by this package; nothing to remove.")
        return
    layout = layout_path(home, manifest["layout"])
    kind = lstat_kind(layout)
    if kind == "link":
        raise RuntimeError(f"Refusing a symbolic link: {layout}")
    if kind not in (None, "file"):
        raise RuntimeError(f"Layout path is not a file: {layout}")
    if kind == "file":
        current = sha256_file(layout)
        if current != manifest.get("layoutHash"):
            raise RuntimeError(
                f"Layout was edited locally: {layout}. No files removed. Move that layout aside before uninstalling."
            )
    if current_layout(home) == manifest["layout"]:
        if lstat_kind(layout_path(home, manifest["source"])) != "file":
            raise RuntimeError(
                f"Original layout {manifest['source']} is missing. Select another layout before uninstalling."
            )
        switch_layout(home, manifest["source"])
    if lstat_kind(layout) == "file":
        if sha256_file(layout) != manifest.get("layoutHash"):
            raise RuntimeError(f"Layout changed during uninstall: {layout}. No mod files removed.")
        layout.unlink()
        print(f"Removed layout {manifest['layout']}.")
    state = state_path(home, manifest["layout"])
    if lstat_kind(state) == "link":
        raise RuntimeError(f"Refusing a symbolic link: {state}")
    if lstat_kind(state) == "file":
        state.unlink()
    removed, remaining = remove_owned_files(home, manifest.get("files") or {})
    print(f"Removed {len(removed)} unchanged owned file(s).")
    if remaining:
        manifest["files"] = remaining
        write_atomic(manifest_path(home), (json.dumps(manifest, indent=2) + "\n").encode("utf-8"))
        print(f"Kept edited files. The install record remains at {manifest_path(home)}.")
        return
    remove_generated(home)
    record = manifest_path(home)
    if lstat_kind(record) == "file":
        record.unlink()
    if lstat_kind(data_dir(home)) == "dir":
        try:
            data_dir(home).rmdir()
        except OSError:
            pass
    print(f"Restored {manifest['source']}. Cursor usage files are gone.")
