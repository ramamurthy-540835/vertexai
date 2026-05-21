# clients/servicenow_client.py

import requests
import urllib3
import pandas as pd

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class ServiceNowClient:

    def __init__(self, config):

        self.instance = config["servicenow"]["instance"]
        self.table = config["servicenow"]["table"]
        self.client_id = config["servicenow"]["client_id"]
        self.client_secret = config["servicenow"]["client_secret"]

        self.base_url = f"https://{self.instance}"

        self.access_token = self.generate_token()

    # ---------------------------------------------------
    # GENERATE TOKEN
    # ---------------------------------------------------

    def generate_token(self):

        token_url = f"{self.base_url}/oauth_token.do"

        payload = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret
        }

        response = requests.post(
            token_url,
            data=payload,
            verify=False
        )

        response.raise_for_status()

        return response.json()["access_token"]

    # ---------------------------------------------------
    # FETCH LEADS
    # ---------------------------------------------------

    def fetch_leads(self):

        url = f"{self.base_url}/api/now/table/{self.table}"

        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json"
        }

        params = {

            "sysparm_query":
                "account.sys_updated_onONToday@javascript:gs.beginningOfToday()@javascript:gs.endOfToday()^accountSTARTSWITHTEST - 06",

            "sysparm_fields":
                ",".join([
                    "sys_id",
                    "number",
                    "sys_updated_on",

                    "u_warehouse_number",
                    "u_lead_source_new",
                    "u_membership_number",

                    "account.name",
                    "account.street",
                    "account.city",
                    "account.state",
                    "account.u_zip_code",
                    "account.u_phone",
                    "account.u_contact_email",
                    "account.u_bd_industry_new",
                    "account.u_industry_codes",
                    "account.notes"
                ]),

            "sysparm_display_value": "true",
            "sysparm_limit": "1000"
        }

        response = requests.get(
            url,
            headers=headers,
            params=params,
            verify=False
        )

        response.raise_for_status()

        data = response.json()["result"]

        return pd.DataFrame(data)
    
    # ---------------------------------------------------
    # FETCH POS RECORDS
    # ---------------------------------------------------

    def fetch_pos_record(
        self,
        oms_company=None
    ):

        # TODO
        # Replace after POS API details shared

        print("\nFetching POS record from ServiceNow")

        print(f"OMS Company: {oms_company}")

        return pd.DataFrame()