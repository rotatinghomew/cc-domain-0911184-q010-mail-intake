FROM python:3.11-slim-bookworm@sha256:65a93d69fa75478d554f4ad27c85c1e69fa184956261b4301ebaf6dbb0a3543d
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements-build.txt /app/requirements-build.txt
RUN python3 -m pip install --no-cache-dir -r requirements-build.txt
COPY . /app
CMD ["python3", "demo.py"]
