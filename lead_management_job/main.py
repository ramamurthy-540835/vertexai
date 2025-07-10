import sys
import uuid
import os
from costco.leadmgmt.config.Configuration import JobConfig
from costco.leadmgmt.sync_snow_gcp import get_data_from_snow, load_data_to_db, write_lead_data_to_db, \
    write_pos_data_to_db, read_lead_data, read_pos_data, get_record_snow_to_gcs, trigger_match_job

if __name__ == "__main__":
    config_path = os.environ.get("CONFIG_FILE_PATH")
    job_config = JobConfig(config_path)

    print("inside main")
    if len(sys.argv) == 1:
        print("error : required argument to run specific job ")

    stage = sys.argv[1]
    if stage.lower() == "snow_to_gcs":
        try:
            batch_id = uuid.uuid4()
            get_data_from_snow(batch_id, job_config)
        except Exception as ex:
            print("Error happened during snow_to_gcs process")
            print(ex)
            raise ex
    elif stage.lower() == "snow_validation":
        try:
            import json
            import requests
            url = "https://costcobizsvctest.service-now.com/api/sn_retail/lead_pos_data/getLead"
            username = 'lead.api.access'
            password = 'Costco@web123'

            payload = json.dumps({
            "start_index": "1",
            "end_index": "5",
            "start_date": "2025-05-05",
            "end_date": "2025-07-17"
            })
            headers = {
                    'Content-Type': 'application/json',
                    'Accept': 'application/json'
                }
                
            auth = (username, password) 

            response = requests.request("POST", url, headers=headers, data=payload, auth=auth)

            print(response.text)
            print("Snow validation successfully completed")
        except Exception as ex:
            print("Error happened during snow_validation process")
            print(ex)
    elif stage.lower() == "gcs_to_db":
        try:
            batch_id = uuid.uuid4()
            load_data_to_db(batch_id, job_config)
        except Exception as ex:
            print("Error happened during gcs_to_db process")
            print(ex)
            raise ex
    elif stage.lower() == "snow_to_db":
        try:
            batch_id = uuid.uuid4()
            get_data_from_snow(batch_id, job_config)
            load_data_to_db(batch_id, job_config)
        except Exception as ex:
            print("Error happened during snow_to_db process")
            print(ex)
            raise ex
    elif stage.lower() == "lead_to_db":
        try:
            batch_id = uuid.uuid4()
            write_lead_data_to_db(batch_id, job_config)
        except Exception as ex:
            print("Error happened during write_lead_data_to_db process")
            print(ex)
            raise ex
    elif stage.lower() == "pos_to_db":
        try:
            batch_id = uuid.uuid4()
            write_pos_data_to_db(batch_id, job_config)
        except Exception as ex:
            print("Error happened during write_pos_data_to_db process")
            print(ex)
            raise ex
    elif stage.lower() == "snow_to_gcs_lead":
        try:
            batch_id = uuid.uuid4()
            get_record_snow_to_gcs(batch_id, "lead",job_config)
        except Exception as ex:
            print("Error happened during read_lead_data process")
            print(ex)
            raise ex
    elif stage.lower() == "snow_to_gcs_pos":
        try:
            batch_id = uuid.uuid4()
            get_record_snow_to_gcs(batch_id, "pos",job_config )
        except Exception as ex:
            print("Error happened during read_pos_data process")
            print(ex)
            raise ex
    elif stage.lower() == "match_job":
        try:
            trigger_match_job(job_config)
        except Exception as ex:
            print("Error happened during trigger_match_job process")
            print(ex)
            raise ex
    else:
        print("Invalid Argument - Valid arguments are [snow_to_gcs,gcs_to_db ,snow_to_db]")
        raise Exception("Invalid Arguments - Valid arguments are snow_to_gcs,gcs_to_db ,snow_to_db")
