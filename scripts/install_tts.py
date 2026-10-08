"""Download pinned MLX model snapshots after syncing tts_runtime dependencies."""

from pathlib import Path

from huggingface_hub import snapshot_download

ROOT = Path(__file__).resolve().parents[1]
MODELS = {
    "custom": (
        "mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit",
        "41d3337e8b7f2843a75841595fc14e4b9a7a4b96",
    ),
    "base": (
        "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit",
        "e7dd0585652209fa0d7783659aad4e8a324de11c",
    ),
}

if __name__ == "__main__":
    for name, (repo, revision) in MODELS.items():
        snapshot_download(repo, revision=revision, local_dir=ROOT / "models/tts" / name)
    print("Pinned TTS models ready")
