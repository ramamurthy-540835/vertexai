import subprocess


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


print("\n==============================")
print("ALL STEPS COMPLETED")
print("==============================\n")