import argparse
import shlex
import shutil
import subprocess
import sys
import venv
from pathlib import Path


ROOT = Path(__file__).resolve().parent
VENV_DIR = ROOT / ".venv"


def venv_python() -> Path:
    executable = "python.exe" if sys.platform == "win32" else "python"
    scripts_dir = "Scripts" if sys.platform == "win32" else "bin"
    return VENV_DIR / scripts_dir / executable


def run(command: list[str], dry_run: bool) -> None:
    print(f"$ {shlex.join(command)}", flush=True)
    if not dry_run:
        subprocess.run(command, cwd=ROOT, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Install project dependencies and start the Docker Compose stack.")
    parser.add_argument("--dry-run", action="store_true", help="show setup actions without running them")
    args = parser.parse_args()

    requirements = [ROOT / "requirements.txt", ROOT / "requirements-dev.txt", ROOT / ".env.example"]
    missing_files = [path.name for path in requirements if not path.is_file()]
    if missing_files:
        parser.error(f"required setup files are missing: {', '.join(missing_files)}")

    docker = shutil.which("docker")
    if not docker:
        parser.error("Docker CLI was not found; install Docker Desktop with the Compose plugin")
    if not args.dry_run:
        run([docker, "compose", "version"], dry_run=False)

    python = venv_python()
    if not python.exists():
        print(f"Creating virtual environment at {VENV_DIR}.", flush=True)
        if not args.dry_run:
            venv.EnvBuilder(with_pip=True).create(VENV_DIR)

    run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "-r",
            str(ROOT / "requirements.txt"),
            "-r",
            str(ROOT / "requirements-dev.txt"),
        ],
        dry_run=args.dry_run,
    )

    env_file = ROOT / ".env"
    if env_file.exists():
        print(".env already exists; leaving it unchanged.")
    else:
        print("Creating .env from .env.example.")
        if not args.dry_run:
            shutil.copy2(ROOT / ".env.example", env_file)

    run([docker, "compose", "up", "--build"], dry_run=args.dry_run)


if __name__ == "__main__":
    main()