import os
from flask import Flask
app = Flask(__name__)

# pip install google-cloud-run
from google.cloud import run_v2

@app.route('/')
def hello():

    PROJECT_ID = os.environ.get("PROJECT_ID")

    client = run_v2.JobsClient()

    # UPDATE TO YOUR JOB NAME, REGION, AND PROJECT ID
    job_name = f'projects/{PROJECT_ID}/locations/us-central1/jobs/snow-sync-job' 

    print("Triggering job...")
    request = run_v2.RunJobRequest(name=job_name)
    operation = client.run_job(request=request)
    response = operation.result()

    print(response)
    return "Done!"

if __name__ == '__main__':
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))