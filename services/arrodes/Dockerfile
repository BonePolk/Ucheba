FROM python:3.12-slim
WORKDIR /app
COPY common/requirements.txt /tmp/common-requirements.txt
COPY services/arrodes/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r /tmp/common-requirements.txt -r requirements.txt
COPY common ./common
COPY services/arrodes .
RUN apt-get update && apt-get install -y --no-install-recommends util-linux \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -g 10001 arrodes && useradd -m -d /home/codex -u 10001 -g 10001 arrodes \
    && python -c "from pathlib import Path; from openai_codex.client import CodexConfig, _resolve_codex_bin; Path('/usr/local/bin/arrodes-native').symlink_to(_resolve_codex_bin(CodexConfig()))"
COPY services/arrodes/arrodes-worker /usr/local/bin/arrodes-worker
RUN chmod 755 /usr/local/bin/arrodes-worker
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
