from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, text, BigInteger
from sqlalchemy.exc import SQLAlchemyError
from costco.leadmgmt.database.DBUtil import DatabaseDetail, TransactionBO


def add_batch_id(batch_id: str, data_type: str, stage: str, status: str,
                 db_config):
    try:

        params = {"batch_id": batch_id, "data_type": data_type, "stage": stage, "status": status}
        insert_query = f"insert into {db_config.schema_name}.batch_audit (batch_id,data_type,stage,status) values( :batch_id,:data_type" \
                       f",:stage,:status);"
        with db_config.get_engine().begin() as conn:
            conn.execute(text(insert_query), params)
            print(f"batch id added successfully - {batch_id}")
    except  SQLAlchemyError as e:
        print(f"Database error while adding data to batch audit: {e}")

        raise e


def update_batch_id(batch_id: str, data_type: str, stage: str, total_count: int, success_count: int, status: str,
                    db_config: DatabaseDetail):
    try:

        schema = db_config.schema_name
        params = {"batch_id": batch_id, "data_type": data_type, "total_volume": total_count,
                  "success_count": success_count,
                  "status": status, "stage": stage}
        update_query = f"update {schema}.batch_audit set total_volume=:total_volume, success_count =:success_count, " \
                       f"end_date=current_timestamp ,status =:status where batch_id = :batch_id and data_type = :data_type and stage =:stage ;"
        with db_config.get_engine().begin() as conn:
            conn.execute(text(update_query), params)
            print(f"batch id update successfully - {batch_id}")
    except  SQLAlchemyError as e:
        print(f"Database error while adding data to batch audit: {e}")
        with db_config.get_engine().connect() as conn:
            conn.execute(text("ROLLBACK"))  # Reset transaction state
        raise e


def get_latest_batch_id(db_config: DatabaseDetail, data_type, stage):
    batch_id = None
    status = None
    try:

        schema = db_config.schema_name
        params = {"data_type": data_type, "stage": stage}
        select_query = f"select batch_id,status from {schema}.batch_audit where  data_type = :data_type and stage =:stage order by load_date desc limit 1 ;"
        print(select_query)
        with db_config.get_engine().begin() as conn:
            result = conn.execute(text(select_query), params)
            first_result = result.first()
            if first_result:
                batch_id = first_result[0]
                status = first_result[1]
            return batch_id, status
    except Exception as ex:
        print("Error happened reading  batch audit data")
        print(ex)
        raise ex


def get_latest_batch_by_status(db_config: DatabaseDetail, data_type, stage, status):
    batch_id = None
    load_date = None

    try:

        schema = db_config.schema_name
        params = {"data_type": data_type, "stage": stage, "status": status}
        select_query = (f"select batch_id,load_date from {schema}.batch_audit where  data_type = :data_type and "
                        f"stage =:stage and lower(status) = lower(:status) order by load_date desc limit 1 ;")
        print(select_query)
        with db_config.get_engine().begin() as conn:
            result = conn.execute(text(select_query), params)
            first_result = result.first()
            if first_result:
                batch_id = first_result[0]
                load_date = first_result[1]
            return batch_id, load_date
    except Exception as ex:
        print("Error happened reading  batch audit data")
        print(ex)
        raise ex

