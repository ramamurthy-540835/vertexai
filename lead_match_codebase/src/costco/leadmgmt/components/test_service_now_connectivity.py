try:
    import json
    import requests
    def check_connection():
        url = "https://costcobizsvctest.service-now.com/api/sn_retail/lead_pos_data/getLead"
        username = 'lead.api.access'
        password = 'Costco@web123'

        payload = json.dumps({
        "start_index": "1",
        "end_index": "5",
        "start_date": "2025-05-05",
        "end_date": "2025-06-17"
        })
        headers = {
                'Content-Type': 'application/json',
                'Accept': 'application/json'
            }
            
        auth = (username, password) 

        response = requests.request("POST", url, headers=headers, data=payload, auth=auth)
        print(response.text)
except Exception as ex:
    print("Error happened during snow_validation process")
    print(ex)