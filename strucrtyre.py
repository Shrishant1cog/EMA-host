from pathlib import Path

# ============================================================
# EMA PROJECT STRUCTURE EXPORTER
# Creates: project-tree.txt
# ============================================================

ROOT = Path.cwd()
OUTPUT_FILE = ROOT / "project-tree.txt"

# Directories to exclude
EXCLUDED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    "node_modules",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".idea",
    ".vscode",
}

# Files to exclude
EXCLUDED_FILES = {
    ".env",
    ".env.local",
    ".env.production",
    ".env.development",
    "project-tree.txt",
}


def should_exclude(path: Path) -> bool:
    """Return True when a file/folder should not appear in the tree."""

    if path.is_dir():
        return path.name in EXCLUDED_DIRS

    return (
        path.name in EXCLUDED_FILES
        or path.suffix.lower() in {".pyc", ".pyo", ".log"}
    )


def write_tree(path: Path, file, prefix: str = ""):
    """Recursively write the directory structure."""

    try:
        items = [
            item
            for item in path.iterdir()
            if not should_exclude(item)
        ]
    except PermissionError:
        file.write(f"{prefix}└── [ACCESS DENIED]\n")
        return

    # Folders first, then files
    items.sort(
        key=lambda item: (
            not item.is_dir(),
            item.name.lower()
        )
    )

    for index, item in enumerate(items):
        is_last = index == len(items) - 1

        branch = "└── " if is_last else "├── "
        child_prefix = prefix + ("    " if is_last else "│   ")

        if item.is_dir():
            file.write(f"{prefix}{branch}{item.name}/\n")
            write_tree(item, file, child_prefix)

        else:
            file.write(f"{prefix}{branch}{item.name}\n")


def main():
    print("=" * 60)
    print("EMA PROJECT STRUCTURE EXPORTER")
    print("=" * 60)

    print(f"\nProject root:")
    print(ROOT)

    with OUTPUT_FILE.open(
        "w",
        encoding="utf-8"
    ) as file:

        file.write("EMA PROJECT STRUCTURE\n")
        file.write("=====================\n\n")
        file.write(f"{ROOT.name}/\n")

        write_tree(ROOT, file)

    print("\nProject tree generated successfully.")
    print(f"\nOutput file:")
    print(OUTPUT_FILE)


if __name__ == "__main__":
    main()