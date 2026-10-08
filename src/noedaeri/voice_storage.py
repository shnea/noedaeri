"""A required NAS mount must never silently fall back to local disk."""

import os


def voice_storage_online(settings):
    return settings.voice_mount_root is None or os.path.ismount(settings.voice_mount_root)
