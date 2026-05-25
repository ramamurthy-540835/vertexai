import subprocess
import os
import configparser

from utils.gcs_utils import download_blob, upload_blob


# ---------------------------------------------------
# CONFIG LOAD
# ---------------------------------------------------
config = configparser.ConfigParser()
config.read("configuration_qa.ini")

bucket_name = config["GCS"]["BUCKET_NAME"]

input_pos_path = config["GCS"]["INPUT_POS_PATH"]
pos_file_name = config["GCS"]["POS_FILE_NAME"]

output_pos_mapping_path = config["GCS"]["OUTPUT_POS_MAPPING_PATH"]
output_match_validation_path = config["GCS"]["OUTPUT_MATCH_VALIDATION_PATH"]
output_lead_validation_path = config["GCS"]["OUTPUT_LEAD_VALIDATION_PATH"]
output_match_sync_validation_path = config["GCS"]["OUTPUT_MATCH_SYNC_VALIDATION_PATH"]

# ---------------------------------------------------
# TMP DIRS
# ---------------------------------------------------
TMP_INPUT_DIR = "/tmp/input"

TMP_OUTPUT_DIR = "/tmp/output"

TMP_POS_MAPPING_DIR = "/tmp/output/pos_mapping"

TMP_MATCH_VALIDATION_DIR = "/tmp/output/match_validation"

TMP_LEAD_VALIDATION_DIR = "/tmp/output/lead_validation"

TMP_MATCH_SYNC_VALIDATION_DIR = "/tmp/output/match_sync_validation"

# ---------------------------------------------------
# CREATE DIRS
# ---------------------------------------------------

os.makedirs(TMP_INPUT_DIR, exist_ok=True)

os.makedirs(TMP_OUTPUT_DIR, exist_ok=True)

os.makedirs(TMP_POS_MAPPING_DIR, exist_ok=True)

os.makedirs(TMP_MATCH_VALIDATION_DIR, exist_ok=True)

os.makedirs(TMP_LEAD_VALIDATION_DIR, exist_ok=True)

os.makedirs(TMP_MATCH_SYNC_VALIDATION_DIR, exist_ok=True)


# ---------------------------------------------------
# DOWNLOAD INPUT FILE
# ---------------------------------------------------
gcs_input_file = f"{input_pos_path}{pos_file_name}"

local_input_file = f"{TMP_INPUT_DIR}/{pos_file_name}"

print("\n==============================")
print("DOWNLOADING INPUT FILE")
print("==============================\n")

download_blob(
    bucket_name,
    gcs_input_file,
    local_input_file
)

print(f"Local input file: {local_input_file}")

# ---------------------------------------------------
# SET ENV VARIABLES
# ---------------------------------------------------

os.environ["POS_INPUT_FILE"] = local_input_file

os.environ["TMP_OUTPUT_DIR"] = TMP_OUTPUT_DIR

os.environ["TMP_POS_MAPPING_DIR"] = TMP_POS_MAPPING_DIR

os.environ["TMP_MATCH_VALIDATION_DIR"] = TMP_MATCH_VALIDATION_DIR

os.environ["TMP_LEAD_VALIDATION_DIR"] = TMP_LEAD_VALIDATION_DIR

os.environ["TMP_MATCH_SYNC_VALIDATION_DIR"] = TMP_MATCH_SYNC_VALIDATION_DIR

# ---------------------------------------------------
# RUN STEP FUNCTION
# ---------------------------------------------------

def run_step(step_name, script_path):

    print(f"\n==============================")
    print(f"RUNNING {step_name}")
    print(f"==============================\n")

    result = subprocess.run(
        ["python", script_path],
        check=False
    )

    if result.returncode != 0:

        raise Exception(
            f"{step_name} failed"
        )

    print(f"\n{step_name} completed successfully\n")


# ---------------------------------------------------
# RUN STEPS
# ---------------------------------------------------

run_step(
    "STEP 1",
    "pos_match_validation/step1_pos_mapping.py"
)

run_step(
    "STEP 2",
    "pos_match_validation/step2_match_validation.py"
)

run_step(
    "STEP 3",
    "pos_match_validation/step3_lead_validation.py"
)

run_step(
    "STEP 4",
    "pos_match_validation/step4_match_sync_validation.py"
)


# ---------------------------------------------------
# UPLOAD OUTPUT FILES
# ---------------------------------------------------

print("\n==============================")
print("UPLOADING OUTPUT FILES")
print("==============================\n")


# ---------------------------------------------------
# POS MAPPING OUTPUT
# ---------------------------------------------------

pos_mapping_file = f"{TMP_POS_MAPPING_DIR}/pos_mapping.csv"

if os.path.exists(pos_mapping_file):

    upload_blob(
        bucket_name,
        pos_mapping_file,
        f"{output_pos_mapping_path}pos_mapping.csv"
    )

else:

    print(f"File not found: {pos_mapping_file}")


# ---------------------------------------------------
# MATCH VALIDATION OUTPUT
# ---------------------------------------------------

match_validation_file = (
    f"{TMP_MATCH_VALIDATION_DIR}/match_validation.csv"
)

if os.path.exists(match_validation_file):

    upload_blob(
        bucket_name,
        match_validation_file,
        f"{output_match_validation_path}match_validation.csv"
    )

else:

    print(f"File not found: {match_validation_file}")


# ---------------------------------------------------
# LEAD VALIDATION OUTPUT
# ---------------------------------------------------

lead_validation_file = (
    f"{TMP_LEAD_VALIDATION_DIR}/lead_validation.csv"
)

if os.path.exists(lead_validation_file):

    upload_blob(
        bucket_name,
        lead_validation_file,
        f"{output_lead_validation_path}lead_validation.csv"
    )

else:

    print(f"File not found: {lead_validation_file}")


# ---------------------------------------------------
# MATCH SYNC VALIDATION OUTPUT
# ---------------------------------------------------

match_sync_validation_file = (
    f"{TMP_MATCH_SYNC_VALIDATION_DIR}/match_sync_validation.csv"
)

if os.path.exists(match_sync_validation_file):

    upload_blob(
        bucket_name,
        match_sync_validation_file,
        f"{output_match_sync_validation_path}match_sync_validation.csv"
    )

else:

    print(f"File not found: {match_sync_validation_file}")


# ---------------------------------------------------
# COMPLETED
# ---------------------------------------------------

print("\n==============================")
print("ALL STEPS COMPLETED")
print("ALL OUTPUTS UPLOADED TO GCS")
print("==============================\n")