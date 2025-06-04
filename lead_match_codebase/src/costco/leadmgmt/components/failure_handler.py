from kfp.dsl import PipelineTaskFinalStatus
import sqlalchemy
from datetime import datetime
from sqlalchemy import text
from costco.leadmgmt.config.Configuration import JobConfig


def execute_failed_query(match_id, failed_status_query,status:PipelineTaskFinalStatus,db_config ):

    #engine
    engine = db_config.get_engine()

    end_date = datetime.now()
    start_date = datetime.now()

    print('Pipeline status: ', status.state)

    # check for failure status
    if status.state == 'FAILED':

        with engine.connect() as connection:
            with connection.begin():  # Automatically commits the transaction
                # Update failed status
                connection.execute(
                    text(failed_status_query),
                    [{'status': 'Failed', 'end_date': end_date, 'start_date': start_date, 'match_id': match_id,
                      'comments': status}]
                )
        return "FAILED"
    else:
        return "pipeline completed successfully"

def failure_handler(match_id:str,status:PipelineTaskFinalStatus,config_file_path:str) -> str:

    #Initialization
    job_config = JobConfig(config_file_path)
    db_config = job_config.db_config
    query_config = job_config.match_query

    #query
    failed_status_query = query_config.failed_status_query

    # call the execute failed query function
    return execute_failed_query(match_id,failed_status_query,status,db_config)