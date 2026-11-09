FROM mcr.microsoft.com/playwright/python:v1.64.1-noble
WORKDIR /app
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
COPY common/requirements.txt /tmp/common-requirements.txt
COPY services/browser/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r /tmp/common-requirements.txt -r requirements.txt \
    && mkdir /browser && chown pwuser:pwuser /browser
COPY common ./common
COPY services/browser .
USER pwuser
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
