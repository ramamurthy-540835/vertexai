# setup.py
import setuptools

setuptools.setup(
    name='pos_pipeline', 
    version='2.0.0',
    packages=setuptools.find_packages(),
    install_requires=[
        'apache-beam[gcp]==2.73.0',
        'psycopg2-binary==2.9.9',
        'cloud-sql-python-connector[pg8000]>=1.7.0',
        'google-auth>=2.29.0',
        'google-cloud-pubsub>=2.21.1',
        'google-cloud-storage>=2.16.0',
        'openpyxl>=3.1.0',
        'xlrd>=2.0.1',
        'pyarrow>=15.0.0',
        'python-dateutil>=2.9.0',
    ]
)
