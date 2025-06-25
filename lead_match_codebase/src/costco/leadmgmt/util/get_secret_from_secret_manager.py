from google.cloud import secretmanager


def access_secret(project_id, secret_id):
    """Retrieve secret value from Google Secret Manager"""
    client = secretmanager.SecretManagerServiceClient()
    secret_name = f"projects/{project_id}/secrets/{secret_id}/versions/latest"

    response = client.access_secret_version(name=secret_name)
    secret_value = response.payload.data.decode("UTF-8")

    return secret_value