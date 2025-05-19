import time

print("Starting Cloud Run Job...")

for i in range(5):
    print(f"Processing step {i + 1}/5")
    time.sleep(1)

print("Cloud Run Job completed.")
