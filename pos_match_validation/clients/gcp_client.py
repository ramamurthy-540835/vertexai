# clients/gcp_client.py

import pandas as pd


class GCPClient:

    def __init__(self, config):

        self.config = config

        # TODO
        # Add DB connection later
        self.connection = None

    # ---------------------------------------------------
    # GENERIC QUERY EXECUTOR
    # ---------------------------------------------------

    def execute_query(self, query):

        print("\nEXECUTING QUERY:")
        print(query)

        # TODO
        # Execute query using actual DB connection
        # Return pandas DataFrame

        return pd.DataFrame()

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
        FROM lead_mgmt_qat.transaction t
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
        account_id=None,
        business_name=None
    ):

        query = """
        SELECT *
        FROM lead_mgmt_qat.account
        WHERE 1=1
        """

        if account_id:
            query += f"\nAND account_id = '{account_id}'"

        if business_name:
            query += f"\nAND business_name = '{business_name}'"

        return self.execute_query(query)