"""
Thin CLI wrapper around audio-separator BS-Roformer.
Called by collect_training_clips.py via .venv_diffsinger python.
Prints the full path to the separated vocals WAV as the last line of stdout.
"""
import argparse
import os
from pathlib import Path

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

_MODEL_CACHE = Path(__file__).parent / "DiffSinger" / "checkpoints" / "audio_separator_models"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", help="Input audio file")
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    _MODEL_CACHE.mkdir(parents=True, exist_ok=True)

    from audio_separator.separator import Separator

    sep = Separator(
        output_dir=args.output_dir,
        model_file_dir=str(_MODEL_CACHE),
        output_format="WAV",
        normalization_threshold=0.9,
    )
    sep.load_model("model_bs_roformer_ep_317_sdr_12.9755.ckpt")
    output_files = sep.separate(args.source)

    # sep.separate() returns bare filenames; resolve to full paths
    output_files = [
        str(Path(args.output_dir) / Path(f).name) if not Path(f).is_absolute() else f
        for f in output_files
    ]

    vocals = next((f for f in output_files if "(Vocals)" in Path(f).name), None)
    if not vocals:
        if output_files:
            vocals = output_files[0]
        else:
            raise RuntimeError(f"BS-Roformer produced no output files")

    print(vocals)


if __name__ == "__main__":
    main()
