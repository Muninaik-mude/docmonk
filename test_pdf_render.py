import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


def run_pdf2htmlex(pdf_path: Path, out_dir: Path, zoom: float | None) -> int:
    exe = shutil.which("pdf2htmlEX")
    if not exe:
        print("pdf2htmlEX not found on PATH.", file=sys.stderr)
        return 2

    out_dir.mkdir(parents=True, exist_ok=True)
    out_html = out_dir / (pdf_path.stem + ".html")

    cmd = [
        exe,
        "--dest-dir", str(out_dir),
        "--embed", "cfi",  # embed css, fonts, images for self-contained output
        "--optimize-text", "1",
        "--correct-text-visibility", "1",
        "--process-outline", "0",
        "--printing", "0",
        "--fit-width", "0",
        "--fit-height", "0",
        "--zoom", str(zoom) if zoom else "1.0",
        str(pdf_path),
        str(out_html),
    ]

    print("Running:", " ".join(cmd))
    return subprocess.call(cmd)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Render PDF to HTML using pdf2htmlEX for layout fidelity."
    )
    parser.add_argument("pdf", help="Path to source PDF")
    parser.add_argument("out", help="Output directory")
    parser.add_argument("--zoom", type=float, default=1.0)
    args = parser.parse_args()

    pdf_path = Path(args.pdf).expanduser().resolve()
    out_dir = Path(args.out).expanduser().resolve()

    if not pdf_path.exists():
        print(f"PDF not found: {pdf_path}", file=sys.stderr)
        return 1

    return run_pdf2htmlex(pdf_path, out_dir, args.zoom)


if __name__ == "__main__":
    raise SystemExit(main())
