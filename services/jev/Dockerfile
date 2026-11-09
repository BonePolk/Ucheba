FROM python:3.12-slim
WORKDIR /app
ENV HF_HOME=/models USE_TF=0 TOKENIZERS_PARALLELISM=false OMP_NUM_THREADS=2
COPY common/requirements.txt /tmp/common-requirements.txt
COPY services/jev/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir -r /tmp/common-requirements.txt -r requirements.txt
COPY common ./common
COPY services/jev .
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
