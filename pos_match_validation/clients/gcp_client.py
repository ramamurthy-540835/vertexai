import pandas as pd

from costco.leadmgmt.config.Configuration import JobConfig


class GCPClient:

    def __init__(self, config_file_path):

        self.job_config = JobConfig(config_file_path)

        self.engine = self.job_config.db_config.get_engine()

    # ---------------------------------------------------
    # COMMON QUERY EXECUTOR
    # ---------------------------------------------------

    def execute_query(self, query):

        print("\nExecuting Query:")
        print(query)

        return pd.read_sql(query, self.engine)

    # ---------------------------------------------------
    # TRANSACTION TABLE
    # ---------------------------------------------------

    def fetch_transaction_data(
        self,
        oms_company=None,
        pos_id=None
    ):

        query = """
        SELECT *
        FROM lead_mgmt_qat.transaction
        WHERE 1=1
        """

        if oms_company:
            query += f"\nAND oms_company = '{oms_company}'"

        if pos_id:
            query += f"\nAND pos_id = '{pos_id}'"

        return self.execute_query(query)

    # ---------------------------------------------------
    # LEAD TABLE
    # ---------------------------------------------------

    def fetch_lead_data(
        self,
        lead_id=None
    ):

        query = """
        SELECT *
        FROM lead_mgmt_qat.lead
        WHERE 1=1
        """

        if lead_id:
            query += f"\nAND lead_id = '{lead_id}'"

        return self.execute_query(query)

    # ---------------------------------------------------
    # ACCOUNT TABLE
    # ---------------------------------------------------

    def fetch_account_data(
        self,
        account_id=None
    ):

        query = """
        SELECT *
        FROM lead_mgmt_qat.account
        WHERE 1=1
        """

        if account_id:
            query += f"\nAND account_id = '{account_id}'"

        return self.execute_query(query)