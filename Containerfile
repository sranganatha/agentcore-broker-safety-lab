FROM docker.io/library/python:3.12-slim

WORKDIR /app
COPY . .

RUN python -m compileall -q agentcore_broker_safety_lab scripts tests \
    && python -m agentcore_broker_safety_lab.validate_fixtures

CMD ["python", "-m", "unittest", "discover", "-s", "tests", "-v"]
