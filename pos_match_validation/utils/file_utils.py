import os
import glob


def get_latest_file(folder_path):

    csv_files = glob.glob(
        f"{folder_path}/*.csv"
    )

    excel_files = glob.glob(
        f"{folder_path}/*.xlsx"
    )

    files = csv_files + excel_files

    if not files:

        raise Exception(
            f"No CSV/XLSX files found in {folder_path}"
        )

    latest_file = max(
        files,
        key=os.path.getctime
    )

    return latest_file