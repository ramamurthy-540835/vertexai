# utils/file_utils.py

import os
import glob


def get_latest_file(folder_path, extension="csv"):

    files = glob.glob(f"{folder_path}/*.{extension}")

    if not files:
        raise Exception(f"No {extension} files found in {folder_path}")

    latest_file = max(files, key=os.path.getctime)

    return latest_file