import sys
import uuid

from costco.leadmgmt.config.Configuration import JobConfig
from costco.leadmgmt.sync_snow_gcp import get_data_from_snow, load_data_to_db, write_lead_data_to_db, \
    write_pos_data_to_db, read_lead_data, read_pos_data, get_record_snow_to_gcs, trigger_match_job

if __name__ == "__main__":

    job_config = JobConfig("configuration.ini")

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
            get_record_snow_to_gcs(batch_id, "lead", job_config)
        except Exception as ex:
            print("Error happened during read_lead_data process")
            print(ex)
            raise ex
    elif stage.lower() == "snow_to_gcs_pos":
        try:
            batch_id = uuid.uuid4()
            get_record_snow_to_gcs(batch_id, "pos", job_config)
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
            raise
    else:
        print("Invalid Argument - Valid arguments are [snow_to_gcs,gcs_to_db ,snow_to_db]")
        raise Exception("Invalid Arguments - Valid arguments are snow_to_gcs,gcs_to_db ,snow_to_db")
