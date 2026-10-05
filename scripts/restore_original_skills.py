#!/usr/bin/env python3
"""Restore the two personal skill entrypoints from retained original files."""

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import tempfile


SKILLS = (
    ("quality-anti-slop", "install-anti-slop", "https://github.com/dmmulroy/anti-slop",
     "c44ef22ca116d0ba62a3ff663a0bd13a3f3fa40b"),
    ("quality-verification-before-completion", "verification-before-completion",
     "https://github.com/obra/superpowers", "8ca22dba9a94f28898bbce59f2537ff4d87c747d"),
)


def atomic_write(path, content):
    descriptor, temporary = tempfile.mkstemp(prefix=".restore-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
        os.chmod(temporary, path.stat().st_mode & 0o777)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Back up and apply changes; default is preview only")
    parser.add_argument("--skills-root", type=Path, default=Path.home() / ".agents/skills")
    args = parser.parse_args()
    root = args.skills_root.expanduser().resolve()
    project = Path(__file__).resolve().parent.parent
    changes = []

    # Check both skills before changing either one.
    for local_name, original_name, repo, commit in SKILLS:
        folder = root / local_name
        source = folder / "references/original-instructions.md"
        target = folder / "SKILL.md"
        provenance = folder / "references/upstream.md"
        expected = project / "skills" / original_name / "SKILL.md"
        for required in (source, target, provenance, expected):
            if not required.is_file() or required.is_symlink():
                raise ValueError(f"Expected a regular, non-symlink file: {required}")
        original = source.read_bytes()
        if original != expected.read_bytes():
            raise ValueError(f"Retained source differs from the reviewed project copy: {source}")
        if local_name == "quality-anti-slop":
            bundle = project / "skills" / original_name
            for subtree in ("assets", "scripts", "references"):
                for reference in (bundle / subtree).rglob("*"):
                    if reference.is_file():
                        installed = folder / reference.relative_to(bundle)
                        if not installed.is_file() or installed.read_bytes() != reference.read_bytes():
                            raise ValueError(f"Supporting file differs or is missing; preserve and review it first: {installed}")
        digest = hashlib.sha256(original).hexdigest()
        record = (
            "# Original skill restoration\n\n"
            f"- Repository: {repo}\n"
            f"- Recorded source commit: `{commit}`\n"
            f"- Original path: `skills/{original_name}/SKILL.md`\n"
            "- SKILL.md restored verbatim from references/original-instructions.md.\n"
            f"- Restored SKILL.md SHA-256: `{digest}`\n"
            "- No summary, translation, or policy changes were applied.\n"
            "- Existing folder name is retained; frontmatter keeps the original skill name.\n"
            "- Supporting files and licenses are unchanged.\n"
            "- Any old project-setup.md is inactive: the original entrypoint does not reference it.\n"
            "- Previous entrypoint and provenance are preserved in the restoration backup.\n"
            "- This restores a retained version, not the latest upstream version.\n"
        ).encode()
        for destination, content in ((target, original), (provenance, record)):
            if destination.read_bytes() != content:
                changes.append((destination, content))
        print(f"Verified original and dependencies: {local_name}")

    if not changes:
        print("Already restored. No changes needed.")
        return
    for destination, _ in changes:
        print(f"Replace: {destination}")
    if not args.apply:
        print("Preview only. Run again with --apply to back up and restore.")
        return

    backup = Path(tempfile.mkdtemp(prefix="original-skill-backup-", dir=root.parent))
    print(f"Backup: {backup}")
    for destination, _ in changes:
        saved = backup / destination.relative_to(root)
        saved.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(destination, saved)
    try:
        for destination, content in changes:
            atomic_write(destination, content)
        for destination, content in changes:
            if destination.read_bytes() != content:
                raise OSError(f"Verification failed: {destination}")
    except BaseException:
        for destination, _ in changes:
            atomic_write(destination, (backup / destination.relative_to(root)).read_bytes())
        raise
    print("Restored both original skill entrypoints and verified exact bytes.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as error:
        raise SystemExit(f"Restoration stopped: {error}")
