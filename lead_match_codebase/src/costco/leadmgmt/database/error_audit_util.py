from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from costco.leadmgmt.util.logger import app_logger


def add_error_audit(entity_type: str, entity_id: str, error_message: str, db_config,batch_id):
    """
    Insert a new error record into the error_audit table.

    Args:
        entity_type (str): The type of entity (e.g., "lead_id", "pos_id").
        entity_id (str): The ID of the entity that caused the error.
        error_message (str): Error details.
        db_config: Database configuration object providing schema_name and engine.

    Raises:
        SQLAlchemyError: If there is an issue with the database transaction.
    """
    try:
        params = {
            "entity_type": entity_type,
            "entity_id": entity_id,
            "error_message": error_message,
            "batch_id" : batch_id
        }

        insert_query = f"""
            INSERT INTO {db_config.schema_name}.error_audit 
            (entity_type, entity_id, error_message,batch_id) 
            VALUES (:entity_type, :entity_id, :error_message,:batch_id);
        """

        with db_config.get_engine().begin() as conn:
            conn.execute(text(insert_query), params)
            app_logger.debug(
                f"Error audit record inserted successfully for entity_id={entity_id}, entity_type={entity_type}"
            )
    except SQLAlchemyError as e:
        app_logger.error(f"Database error while adding to error_audit: {e}")
        raise e
