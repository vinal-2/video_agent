import os
from pathlib import Path

def copy_py_to_txt(directory: Path, overwrite: bool = False) -> None:
    """
    Copies all .py files in the specified directory
    into .txt files with the same base name.

    :param directory: Path object representing target directory
    :param overwrite: If True, overwrite existing .txt files
    """

    if not directory.exists():
        raise FileNotFoundError(f"Directory not found: {directory}")

    py_files = list(directory.glob("*.py"))

    if not py_files:
        print("No .py files found in directory.")
        return

    for py_file in py_files:
        txt_file = py_file.with_suffix(".txt")

        if txt_file.exists() and not overwrite:
            print(f"Skipping (already exists): {txt_file.name}")
            continue

        with py_file.open("r", encoding="utf-8") as source:
            content = source.read()

        with txt_file.open("w", encoding="utf-8") as destination:
            destination.write(content)

        print(f"Created: {txt_file.name}")

    print("\nCompleted.")


if __name__ == "__main__":
    current_directory = Path.cwd()
    copy_py_to_txt(current_directory, overwrite=False)