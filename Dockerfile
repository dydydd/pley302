FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml requirements.txt ./
COPY pley302 ./pley302
RUN pip install --no-cache-dir -r requirements.txt

ENV PLEY302_CONFIG=/app/config.yaml
VOLUME ["/app/config"]
EXPOSE 15896 8098

CMD ["python", "-m", "pley302"]
