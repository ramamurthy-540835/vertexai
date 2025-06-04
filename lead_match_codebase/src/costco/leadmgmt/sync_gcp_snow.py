import os
from datetime import datetime

import requests
from google.cloud import storage
import io
import json

from google.cloud import pubsub_v1
from kfp.registry import RegistryClient


def publish_message(project_id: str, topic_name: str, message: str) -> None:
    """Publishes a message to a Pub/Sub topic."""
    try:
        print("inside publish message method")
        publisher = pubsub_v1.PublisherClient()
        topic_path = publisher.topic_path(project_id, topic_name)

        # Data must be a bytestring
        data = message.encode("utf-8")

        # When you publish a message, the client returns a future.
        future = publisher.publish(topic_path, data=data)
        print(f"Published message to {topic_path}: {future.result()}")

    except Exception as e:
        print(f"Error publishing message: {e}")

def get_snow_data(url, username, password):
    headers = { "Content-Type": "application/json"}
    response = requests.get( url, headers= headers,       auth=(username, password),
    )
    print(response.content)

    return response.json()

def get_snow_data_using_post(url, username, password,payload, ):
    print("inside get_snow_data_using_post ")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    chunk_number = 1

    response = requests.post( url, headers= headers,       auth=(username, password), data=json.dumps(payload))
    if response.status_code != 200:
        print('Status:', response.status_code, 'Headers:', response.headers, 'Error Response:', response.json())
        raise Exception(f"ServiceNow connection issue . {response.status_code}")
    else:
        output_data = response.json()
        return output_data

    return response.json()


def write_to_gcs(data, folder_path, file_name, bucket_name, chunk_number,service_account_path=None,file_type="json"):
    # Initialize client
    if service_account_path:
        client = storage.Client.from_service_account_json(service_account_path)
    else:
        client = storage.Client()  # Uses default credentials

    bucket = client.bucket(bucket_name)
    # Define GCS path
    blob_name = f"{folder_path}/{file_name}_{chunk_number}.{file_type}"

    blob = bucket.blob(blob_name)
    # Upload string data to the blob
    json_string = json.dumps(data, indent=2)
    blob.upload_from_string(json_string)
    print(f"Uploaded {blob_name} with {len(data['result'] )} records")


def test_method():
    # Need to install requests package for python
    # easy_install requests
    import requests
    # Set the request parameters
    url = 'https://costcobizsvc.service-now.com/api/stwc/lead_info/getLead'
    # Eg. User name="admin", Password="admin" for this code sample.

    user = "lead.api.access"
    pwd = "Costco@web123"
    # Set proper headers
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    # Do the HTTP request
    response = requests.post(url, auth=(user, pwd), headers=headers)
    # Check for HTTP codes other than 200
    if response.status_code != 200:
        print('Status:', response.status_code, 'Headers:', response.headers, 'Error Response:', response.json())
        exit()
    # Decode the JSON response into a dictionary and use the data
    data = response.json()
    print(data)


def compile_and_upload_pipeline():
    #pipeline_path = f"{CONFIG['PIPELINE_NAME']}.json"
    pipeline_path=""

    # 1. Compile the pipeline to a local JSON file
    # kfp.compiler.Compiler().compile(my_pipeline, pipeline_path)

    # 2. Upload the compiled json file to your repository in Artifact Registry
    client = RegistryClient(host=f"https://us-central1-kfp.pkg.dev/p-601-lab-bc-leads-mgmt/lead-mgmt-repo")

    version = f"v{datetime.now().strftime('%Y%m%d%H%M')}"

    templateName, versionName = client.upload_pipeline(
        file_name=pipeline_path,
        tags=[version, "latest"],
        extra_headers={"description": "lead matching pipeline template."})

    print(templateName)
    print(versionName)

    full_template_path = f"https://us-central1-kfp.pkg.dev/p-601-lab-bc-leads-mgmt/lead-mgmt-repo/{templateName}/{version}"
    return full_template_path
from google.cloud import run_v2

def sample_run_job(project_id,location,job_name):
    print("inside gcs trigger cloud run job")
    # Create a client
    client = run_v2.JobsClient()
    #job_name = "lead-matching-job"
    # Initialize request argument(s)
    request = run_v2.RunJobRequest(
        name=f"projects/{project_id}/location/{location}/jobs/{job_name}",
    )

    # Make the request
    operation = client.run_job(request=request)

    print("Waiting for operation to complete...")

    #response = operation.result()
    response = operation.close()

    # Handle the response
    print(response)

def snow_test():
    print("inside get service now data ")
    url1 = "https://costcobizsvc.service-now.com/api/now/v1/table/incident/1234234"
    url=   "https://costcobizsvc.service-now.com/api/stwc/lead_info/getLead"
    url =  'https://costcobizsvc.service-now.com/api/stwc/lead_info/getLead'
    url =  'https://costcobizsvc.service-now.com/api/stwc/pos_info/testpost'

    pending_data_load = True
    batch_size = 10
    start_index =1
    chunk_number = 1
    end_index =  batch_size

    project_id = "p-601-lab-bc-leads-mgmt"
    topic_name = "eventarc-us-central1-trigger-pubsub2-870"
    message_to_publish = "Hello, Pub/Sub from Python!"
    location = "us-central1"
    job_name =  "lead-matching-job"
    #publish_message(project_id, topic_name, message_to_publish)
    #out = sample_run_job(project_id,location,job_name)
    #print(out)
    while  pending_data_load:
        user = "lead.api.access"
        password = "Costco@web123"
        print(f"strat index : {start_index} end index :{end_index}")
        pay_load = { "start_index":start_index ,
                     "end_index":end_index ,
                     "start_date": "2025-04-10 00:00:00",
  "end_date": "2025-04-11 12:00:00"
                      }

        print(pay_load)
        folder_path= "staging/lead"
        file_name = "lead_data"
        bucket_name = "leads_management_solution"


        output_data = get_snow_data_using_post( url,user,password,pay_load )
        print(output_data)
        print(f"{start_index} to {end_index}  = {len(output_data['result']['results'])}")
        #if  len(output_data['result']['returned_count']) > 0:
        if int(output_data['result']['returned_count'] ) > 0:
            #write_to_gcs(output_data, folder_path, file_name, bucket_name, chunk_number)
            end_index = end_index+batch_size
            start_index = start_index+batch_size
            chunk_number = chunk_number+ 1
        else:
            pending_data_load = False

        if chunk_number > 4:
            print("chuk number reached more than 4 ")
            break

    print(len(output_data))
    #test_method()

    print("success")
from costco.leadmgmt.config.Configuration import JobConfig
if __name__ == "__main__":
    file_path=r"sync_config.ini"
    #os.environ["DB_SCHEMA"] = "gnanaprakash"

    conf = JobConfig(file_path)
    #data= '{     "case": "lead_id",     "account.u_warehouse_number": "warehouse_number",     "account": "business_name",     "contact": "contact_id",     "account.number": "account_id",     "account.u_industry_code.u_code_value": "industry_code",     "account.u_bd_industry": "bd_industry",     "account.phone": "phone",     "account.street": "address_line_one",     "account.city": "city",     "account.state": "state",     "account.zip": "zip_code",     "account.country": "country",     "u_type": "type",     "u_membership_number": "membership_number",     "u_confidence_level": "confidence_level",     "u_fiscal_year": "fiscal_year",     "u_period": "fiscal_period" }'
    print(conf.transform_config)
    print(conf.storage_config)
    print(conf.db_config)
    print(conf.snow_config)

    #print(json.loads(data))



