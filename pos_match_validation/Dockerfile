FROM python:3.11

WORKDIR /app

COPY . .

RUN pip install --no-cache-dir -r pos_match_validation/requirements.txt

CMD ["python", "pos_match_validation/run_all.py"]