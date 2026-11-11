FROM python:3.12-slim
WORKDIR /app
COPY common/requirements.txt /tmp/common-requirements.txt
COPY services/straus/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r /tmp/common-requirements.txt -r requirements.txt
COPY common ./common
COPY services/straus .
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
